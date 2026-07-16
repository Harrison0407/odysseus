from __future__ import annotations

import ast
from contextlib import contextmanager, redirect_stderr, redirect_stdout
import copy
import hashlib
import hmac
import importlib.util
from io import StringIO
import json
import os
from pathlib import Path
import re
import shutil
import stat
import sys
import tempfile
import unittest
import uuid


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
VALIDATOR_PATH = REPOSITORY_ROOT / "scripts" / "validate_marketmatch_chroma_snapshot.py"
CONTRACT_PATH = (
    REPOSITORY_ROOT
    / "scripts"
    / "offline_package_contracts"
    / "chroma_metadata_export_v1.json"
)
CONTRACT = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
CONTRACT_SHA256 = CONTRACT["canonical_sha256"]
SENTINEL = os.environ["MARKETMATCH_CHROMA_SNAPSHOT_SENTINEL"]
TEST_ROOT = Path(os.environ["MARKETMATCH_CHROMA_SNAPSHOT_TEST_ROOT"])

FORBIDDEN_MODULE_ROOTS = {
    "app",
    "chromadb",
    "core",
    "routes",
    "services",
    "sqlalchemy",
    "src",
    "workers",
}


def _load_validator():
    before = set(sys.modules)
    spec = importlib.util.spec_from_file_location(
        "marketmatch_chroma_snapshot_validator_under_test", VALIDATOR_PATH
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    imported = set(sys.modules) - before
    forbidden = sorted(
        name for name in imported if name.split(".", 1)[0].lower() in FORBIDDEN_MODULE_ROOTS
    )
    if forbidden:
        raise AssertionError(f"forbidden validator imports: {forbidden}")
    return module


validator = _load_validator()


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _canonical_digest(value: dict) -> str:
    payload = copy.deepcopy(value)
    payload.pop("canonical_sha256", None)
    return hashlib.sha256(_canonical_bytes(payload)).hexdigest()


def _json_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False).encode(
        "utf-8"
    ) + b"\n"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _file_entry(path: str, data: bytes) -> dict:
    return {"path": path, "size": len(data), "sha256": _sha256(data)}


def _tree_digest(entries: list[dict]) -> str:
    return _sha256(_canonical_bytes(sorted(entries, key=lambda item: item["path"])))


OWNER_A = "owner_" + "a" * 32
OWNER_B = "owner_" + "b" * 32
PATH_A = "path_" + "c" * 32
WORKFLOW_A = "workflow_" + "d" * 32
DOCUMENT_A = "document_" + "e" * 32
DOCUMENT_B = "document_" + "f" * 32
UPLOAD_A = "upload_" + "1" * 32
RAW_RAG_LEGACY_A = "rr_rag_legacy_" + "2" * 32


def _collection(name: str, count: int, *, pass_2: str | None = None) -> dict:
    mapping = CONTRACT["collection_contract"]["physical_collections"][name]
    first = hashlib.sha256((name + ":pass").encode("utf-8")).hexdigest()
    return {
        "collection_name": name,
        "family": mapping["family"],
        "lane": mapping["lane"],
        "record_count": count,
        "metadata_digest": hashlib.sha256((name + ":metadata").encode("utf-8")).hexdigest(),
        "capture_pass_1_digest": first,
        "capture_pass_2_digest": pass_2 or first,
        "capture_status": "COMPLETE_SYNTHETIC_FIXTURE",
    }


def _record(**changes) -> dict:
    row = {
        "collection_name": "odysseus_rag",
        "raw_record_id_alias": RAW_RAG_LEGACY_A,
        "owner": OWNER_A,
        "source_path_token": PATH_A,
        "call_or_video_id": WORKFLOW_A,
        "document_ids": [DOCUMENT_A, DOCUMENT_B],
        "upload_id": UPLOAD_A,
        "chunk_id": 0,
        "kind": "call_transcript",
        "native_fields": [
            "call_or_video_id",
            "chunk_id",
            "document_ids",
            "kind",
            "owner",
            "source_path_token",
        ],
        "sql_derived_fields": ["upload_id"],
        "operator_supplied_fields": [],
        "validation_findings": [],
    }
    row.update(changes)
    return row


def _attestation(fixture_id: str) -> dict:
    return {
        "attestation_version": 1,
        "fixture_id": fixture_id,
        "source_classification": "SYNTHETIC_DISPOSABLE_FIXTURE",
        "capture_method": "SYNTHETIC_LAUNCHER_FIXTURE",
        "proof_scope": "OBSERVED_PHYSICAL_METADATA_ROWS_ONLY",
        "logical_chunk_completeness": False,
        "real_source_capture_authorized": False,
        "real_chroma_access_performed": False,
        "owner_registry": {
            "active": [OWNER_A, OWNER_B],
            "retired": [],
            "legacy_approved": [],
            "internal": ["internal-tool", "api", "demo", "system"],
        },
        "workflow_registry": [
            {
                "workflow_id": WORKFLOW_A,
                "workflow_type": "call",
                "owner": OWNER_A,
                "source_path_tokens": {
                    "call_transcript": PATH_A,
                    "call_summary": "path_" + "4" * 32,
                    "video_transcript": None,
                    "video_summary": None,
                },
                "document_ids": [DOCUMENT_A, DOCUMENT_B],
                "upload_id": UPLOAD_A,
                "upload_derivation_status": "SUCCESS",
                "upload_derivation_failure_reason": None,
            }
        ],
        "document_registry": [
            {"document_id": DOCUMENT_A, "owner": OWNER_A},
            {"document_id": DOCUMENT_B, "owner": OWNER_A},
        ],
        "upload_registry": [{"upload_id": UPLOAD_A, "owner": OWNER_A}],
        "fixture_attestation": "0" * 64,
    }


def _make_non_market_record(collection_name: str, alias: str, kind: str) -> dict:
    return {
        "collection_name": collection_name,
        "raw_record_id_alias": alias,
        "owner": None,
        "source_path_token": None,
        "call_or_video_id": None,
        "document_ids": [],
        "upload_id": None,
        "chunk_id": None,
        "kind": kind,
        "native_fields": ["kind"],
        "sql_derived_fields": [],
        "operator_supplied_fields": [],
        "validation_findings": ["EMPTY_OWNER"],
    }


class FixtureBuilder:
    def __init__(self, directory: Path):
        self.directory = directory

    def build(
        self,
        *,
        records: list[dict] | None = None,
        collections: list[dict] | None = None,
        declared_collections: list[str] | None = None,
        mutate_attestation=None,
        mutate_manifest=None,
    ) -> Path:
        records = copy.deepcopy([_record()] if records is None else records)
        if collections is None:
            counts: dict[str, int] = {}
            for row in records:
                name = row.get("collection_name")
                if name in CONTRACT["collection_contract"]["physical_collections"]:
                    counts[name] = counts.get(name, 0) + 1
            collections = [_collection(name, count) for name, count in sorted(counts.items())]
            if not collections:
                collections = [_collection("odysseus_rag", 0)]
        else:
            collections = copy.deepcopy(collections)
        declared = (
            list(declared_collections)
            if declared_collections is not None
            else [row["collection_name"] for row in collections]
        )

        self.directory.mkdir(mode=0o700)
        fixture_id = "fixture_" + uuid.uuid4().hex
        collections_bytes = _json_bytes({"collections": collections})
        records_bytes = b"".join(_canonical_bytes(row) + b"\n" for row in records)
        attestation = _attestation(fixture_id)
        if mutate_attestation:
            mutate_attestation(attestation)
        attestation_core = copy.deepcopy(attestation)
        attestation_core.pop("fixture_attestation", None)
        proof_payload = (
            "marketmatch-chroma-snapshot-v1\0"
            + fixture_id
            + "\0"
            + _sha256(collections_bytes)
            + "\0"
            + _sha256(records_bytes)
            + "\0"
            + _sha256(_canonical_bytes(attestation_core))
            + "\0"
            + CONTRACT_SHA256
        ).encode("utf-8")
        attestation["fixture_attestation"] = hmac.new(
            SENTINEL.encode("ascii"), proof_payload, hashlib.sha256
        ).hexdigest()
        attestation_bytes = _json_bytes(attestation)

        payloads = {
            "capture-attestation.json": attestation_bytes,
            "collections.json": collections_bytes,
            "records.jsonl": records_bytes,
        }
        entries = [_file_entry(name, data) for name, data in sorted(payloads.items())]
        manifest = {
            "manifest_version": 1,
            "contract_name": CONTRACT["contract_name"],
            "contract_sha256": CONTRACT_SHA256,
            "source_classification": "SYNTHETIC_DISPOSABLE_FIXTURE",
            "fixture_id": fixture_id,
            "sealed": True,
            "declared_collections": declared,
            "files": entries,
            "tree_sha256": _tree_digest(entries),
            "canonical_sha256": "",
        }
        if mutate_manifest:
            mutate_manifest(manifest)
        manifest["canonical_sha256"] = _canonical_digest(manifest)
        payloads["snapshot-manifest.json"] = _json_bytes(manifest)

        for name, data in payloads.items():
            path = self.directory / name
            path.write_bytes(data)
            path.chmod(0o400)
        self.directory.chmod(0o500)
        return self.directory


def _snapshot_tree(root: Path) -> tuple:
    rows = []
    for path in sorted([root, *root.rglob("*")], key=lambda item: str(item)):
        metadata = path.lstat()
        digest = None
        if stat.S_ISREG(metadata.st_mode):
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        rows.append(
            (
                str(path.relative_to(root.parent)),
                metadata.st_dev,
                metadata.st_ino,
                stat.S_IFMT(metadata.st_mode),
                stat.S_IMODE(metadata.st_mode),
                metadata.st_nlink,
                metadata.st_size,
                digest,
            )
        )
    return tuple(rows)


@contextmanager
def _writable_snapshot(path: Path):
    path.chmod(0o700)
    for child in path.iterdir():
        if child.is_file() and not child.is_symlink():
            child.chmod(0o600)
    try:
        yield
    finally:
        for child in path.iterdir():
            if child.is_file() and not child.is_symlink():
                child.chmod(0o400)
        path.chmod(0o500)


class ChromaSnapshotValidatorTests(unittest.TestCase):
    def setUp(self):
        snapshots = TEST_ROOT / "snapshots"
        self.snapshot = Path(tempfile.mkdtemp(prefix="fixture-", dir=snapshots))
        self.snapshot.rmdir()
        self.builder = FixtureBuilder(self.snapshot)

    def tearDown(self):
        if self.snapshot.exists() or self.snapshot.is_symlink():
            if self.snapshot.is_symlink():
                self.snapshot.unlink()
                return
            self.snapshot.chmod(0o700)
            for path in sorted(self.snapshot.rglob("*"), reverse=True):
                if path.is_symlink():
                    path.unlink()
                elif path.is_dir():
                    path.chmod(0o700)
                else:
                    path.chmod(0o600)
            shutil.rmtree(self.snapshot)

    def relative(self) -> str:
        return str(self.snapshot.relative_to(TEST_ROOT))

    def validate(self):
        return validator.validate_fixture_snapshot(self.relative(), fixture_only=True)

    def assert_error(self, code: str):
        with self.assertRaises(validator.SnapshotError) as caught:
            self.validate()
        self.assertEqual(code, caught.exception.code)

    def test_valid_synthetic_snapshot(self):
        self.builder.build()
        report = self.validate()
        self.assertEqual("VALID_SYNTHETIC_SNAPSHOT", report["status"])
        self.assertTrue(report["valid"])
        self.assertFalse(report["complete"])
        self.assertFalse(report["approved"])
        self.assertEqual(1, report["record_count"])

    def test_fixture_only_flag_and_runtime_environment_are_required(self):
        self.builder.build()
        with self.assertRaises(validator.SnapshotError) as caught:
            validator.validate_fixture_snapshot(self.relative(), fixture_only=False)
        self.assertEqual("FIXTURE_ONLY_FLAG_REQUIRED", caught.exception.code)
        os.environ["DATABASE_URL"] = "sqlite:///PROHIBITED_RUNTIME_CANARY"
        try:
            with self.assertRaises(validator.SnapshotError) as caught:
                self.validate()
            self.assertEqual("RUNTIME_ENVIRONMENT_PRESENT", caught.exception.code)
        finally:
            os.environ.pop("DATABASE_URL", None)

    def test_launcher_inode_contract_and_sha256_are_pinned(self):
        launcher = REPOSITORY_ROOT / "tests" / "run_offline_chroma_export_tests.sh"
        digest = hashlib.sha256(launcher.read_bytes()).hexdigest()
        self.assertEqual(CONTRACT["fixture_gate"]["required_launcher_sha256"], digest)
        self.assertEqual(validator.TEST_LAUNCHER_SHA256, digest)

    def test_all_families_and_legacy_custom_fastembed_collections(self):
        names = list(CONTRACT["collection_contract"]["physical_collections"])
        records = [
            _make_non_market_record(
                name,
                "rr_{}_{}_{}".format(
                    CONTRACT["collection_contract"]["physical_collections"][name]["family"],
                    CONTRACT["collection_contract"]["physical_collections"][name]["lane"],
                    f"{index + 10:032x}",
                ),
                {
                    "rag": "non_marketmatch_rag",
                    "memory": "memory",
                    "tool_index": "tool_index",
                }[CONTRACT["collection_contract"]["physical_collections"][name]["family"]],
            )
            for index, name in enumerate(names)
        ]
        collections = [_collection(name, 1) for name in names]
        self.builder.build(records=records, collections=collections)
        report = self.validate()
        self.assertEqual(9, report["collection_count"])
        self.assertEqual({"legacy", "custom", "fastembed"}, set(report["lanes"]))
        self.assertEqual({"rag", "memory", "tool_index"}, set(report["families"]))

    def test_unknown_collection_fails_closed(self):
        self.builder.build(
            records=[],
            collections=[
                {
                    **_collection("odysseus_rag", 0),
                    "collection_name": "odysseus_rag_experimental",
                }
            ],
        )
        self.assert_error("UNKNOWN_OR_UNSUPPORTED")

    def test_collection_outside_allowlist_is_unknown(self):
        self.builder.build(
            records=[],
            collections=[
                {
                    **_collection("odysseus_rag", 0),
                    "collection_name": "third_party_collection",
                }
            ],
        )
        self.assert_error("UNKNOWN_OR_UNSUPPORTED")

    def test_missing_declared_collection(self):
        self.builder.build(
            records=[],
            collections=[_collection("odysseus_rag", 0)],
            declared_collections=["odysseus_rag", "odysseus_rag_custom"],
        )
        self.assert_error("DECLARED_COLLECTION_MISSING")

    def test_undeclared_record_collection(self):
        row = _record(collection_name="odysseus_rag_custom")
        self.builder.build(
            records=[row], collections=[_collection("odysseus_rag", 0)]
        )
        self.assert_error("RECORD_COLLECTION_UNDECLARED")

    def test_count_mismatch(self):
        self.builder.build(collections=[_collection("odysseus_rag", 2)])
        self.assert_error("RECORD_COUNT_MISMATCH")

    def test_pass_digest_mismatch(self):
        self.builder.build(
            collections=[_collection("odysseus_rag", 1, pass_2="9" * 64)]
        )
        self.assert_error("CAPTURE_PASS_MISMATCH")

    def test_duplicate_record_alias_within_collection(self):
        self.builder.build(records=[_record(), _record(chunk_id=1)])
        self.assert_error("DUPLICATE_RECORD_ALIAS")

    def test_same_alias_across_lanes(self):
        second = _record(collection_name="odysseus_rag_custom", chunk_id=1)
        self.builder.build(
            records=[_record(), second],
            collections=[
                _collection("odysseus_rag", 1),
                _collection("odysseus_rag_custom", 1),
            ],
        )
        self.assert_error("ALIAS_REUSED_ACROSS_COLLECTIONS")

    def test_repeated_chunk_ordinal_is_accepted(self):
        second = _record(
            raw_record_id_alias="rr_rag_legacy_" + "3" * 32,
            kind="call_summary",
            source_path_token="path_" + "4" * 32,
        )
        self.builder.build(records=[_record(), second])
        report = self.validate()
        self.assertEqual(2, report["record_count"])

    def test_chunk_id_cannot_be_record_identifier(self):
        self.builder.build(records=[_record(raw_record_id_alias="0")])
        self.assert_error("RAW_RECORD_ALIAS_INVALID")

    def test_missing_owner_is_reported(self):
        row = _make_non_market_record(
            "odysseus_rag", RAW_RAG_LEGACY_A, "non_marketmatch_rag"
        )
        self.builder.build(records=[row])
        self.assertIn("EMPTY_OWNER", self.validate()["findings"])

    def test_internal_owner_is_reported(self):
        row = _make_non_market_record(
            "odysseus_rag", RAW_RAG_LEGACY_A, "non_marketmatch_rag"
        )
        row.update(
            owner="system",
            native_fields=["kind", "owner"],
            validation_findings=["INTERNAL_OWNER"],
        )
        self.builder.build(records=[row])
        self.assertIn("INTERNAL_OWNER", self.validate()["findings"])

    def test_retired_owner_is_reported(self):
        def mutate(value):
            value["owner_registry"]["active"].remove(OWNER_A)
            value["owner_registry"]["retired"].append(OWNER_A)

        row = _make_non_market_record(
            "odysseus_rag", RAW_RAG_LEGACY_A, "non_marketmatch_rag"
        )
        row.update(
            owner=OWNER_A,
            native_fields=["kind", "owner"],
            validation_findings=["RETIRED_OWNER"],
        )
        self.builder.build(records=[row], mutate_attestation=mutate)
        self.assertIn("RETIRED_OWNER", self.validate()["findings"])

    def test_unknown_owner_is_reported(self):
        unknown = "owner_" + "9" * 32
        row = _make_non_market_record(
            "odysseus_rag", RAW_RAG_LEGACY_A, "non_marketmatch_rag"
        )
        row.update(
            owner=unknown,
            native_fields=["kind", "owner"],
            validation_findings=["UNKNOWN_OWNER"],
        )
        self.builder.build(records=[row])
        self.assertIn("UNKNOWN_OWNER", self.validate()["findings"])

    def test_cross_owner_workflow_linkage(self):
        def mutate(value):
            value["workflow_registry"][0]["owner"] = OWNER_B
            value["workflow_registry"][0]["upload_derivation_status"] = "FAILED"
            value["workflow_registry"][0]["upload_derivation_failure_reason"] = "OWNER_MISMATCH"

        self.builder.build(
            records=[
                _record(
                    upload_id=None,
                    sql_derived_fields=[],
                    validation_findings=[
                        "CROSS_OWNER_WORKFLOW_LINKAGE",
                        "UPLOAD_DERIVATION_OWNER_MISMATCH",
                    ],
                )
            ],
            mutate_attestation=mutate,
        )
        self.assertIn("CROSS_OWNER_WORKFLOW_LINKAGE", self.validate()["findings"])

    def test_cross_owner_document_linkage(self):
        def mutate(value):
            value["document_registry"][0]["owner"] = OWNER_B
            value["workflow_registry"][0]["upload_derivation_status"] = "FAILED"
            value["workflow_registry"][0]["upload_derivation_failure_reason"] = "DOCUMENT_LINK_CONFLICT"

        self.builder.build(
            records=[
                _record(
                    upload_id=None,
                    sql_derived_fields=[],
                    validation_findings=[
                        "CROSS_OWNER_DOCUMENT_LINKAGE",
                        "UPLOAD_DERIVATION_DOCUMENT_LINK_CONFLICT",
                    ],
                )
            ],
            mutate_attestation=mutate,
        )
        self.assertIn("CROSS_OWNER_DOCUMENT_LINKAGE", self.validate()["findings"])

    def test_cross_owner_upload_linkage(self):
        def mutate(value):
            value["upload_registry"][0]["owner"] = OWNER_B
            value["workflow_registry"][0]["upload_derivation_status"] = "FAILED"
            value["workflow_registry"][0]["upload_derivation_failure_reason"] = "UPLOAD_OWNER_MISMATCH"

        self.builder.build(
            records=[
                _record(
                    upload_id=None,
                    sql_derived_fields=[],
                    validation_findings=[
                        "CROSS_OWNER_UPLOAD_LINKAGE",
                        "UPLOAD_DERIVATION_UPLOAD_OWNER_MISMATCH",
                    ],
                )
            ],
            mutate_attestation=mutate,
        )
        self.assertIn("CROSS_OWNER_UPLOAD_LINKAGE", self.validate()["findings"])

    def test_missing_sql_workflow_derivation(self):
        row = _record(
            upload_id=None,
            sql_derived_fields=[],
            validation_findings=["UPLOAD_DERIVATION_MISSING_WORKFLOW"],
        )

        def mutate(value):
            value["workflow_registry"] = []

        self.builder.build(records=[row], mutate_attestation=mutate)
        self.assertIn("UPLOAD_DERIVATION_MISSING_WORKFLOW", self.validate()["findings"])

    def test_ambiguous_sql_derivation(self):
        def mutate(value):
            value["workflow_registry"].append(copy.deepcopy(value["workflow_registry"][0]))

        self.builder.build(mutate_attestation=mutate)
        self.assert_error("WORKFLOW_REGISTRY_COLLISION")

    def test_native_and_sql_derived_disagreement_is_rejected(self):
        conflicting_upload = "upload_" + "8" * 32
        self.builder.build(records=[_record(upload_id=conflicting_upload)])
        self.assert_error("STRICT_SQL_DERIVATION_INVALID")

    def test_declared_upload_derivation_failure_reason_is_verified(self):
        row = _record(
            source_path_token="path_" + "7" * 32,
            upload_id=None,
            sql_derived_fields=[],
            validation_findings=["UPLOAD_DERIVATION_SOURCE_MISMATCH"],
        )

        def mutate(value):
            value["workflow_registry"][0]["upload_derivation_status"] = "FAILED"
            value["workflow_registry"][0]["upload_derivation_failure_reason"] = "SOURCE_MISMATCH"

        self.builder.build(records=[row], mutate_attestation=mutate)
        self.assertIn("UPLOAD_DERIVATION_SOURCE_MISMATCH", self.validate()["findings"])

    def test_false_upload_derivation_failure_reason_is_rejected(self):
        row = _record(upload_id=None, sql_derived_fields=[])

        def mutate(value):
            value["workflow_registry"][0]["upload_derivation_status"] = "FAILED"
            value["workflow_registry"][0]["upload_derivation_failure_reason"] = "SOURCE_MISMATCH"

        self.builder.build(records=[row], mutate_attestation=mutate)
        self.assert_error("UPLOAD_DERIVATION_REASON_INVALID")

    def test_non_marketmatch_record_cannot_carry_sql_linkage(self):
        row = _make_non_market_record(
            "odysseus_rag", RAW_RAG_LEGACY_A, "non_marketmatch_rag"
        )
        row.update(upload_id=UPLOAD_A, sql_derived_fields=["upload_id"])
        self.builder.build(records=[row])
        self.assert_error("NON_MARKETMATCH_LINKAGE_INVALID")

    def test_strict_derivation_requires_a_source_token(self):
        native = [
            field for field in _record()["native_fields"] if field != "source_path_token"
        ]
        self.builder.build(
            records=[_record(source_path_token=None, native_fields=native)]
        )
        self.assert_error("STRICT_SQL_DERIVATION_INVALID")

    def test_upload_id_marked_native_is_rejected(self):
        self.builder.build(
            records=[
                _record(
                    native_fields=_record()["native_fields"] + ["upload_id"],
                    sql_derived_fields=[],
                )
            ]
        )
        self.assert_error("UPLOAD_ID_MARKED_NATIVE")

    def test_derived_value_presented_as_native_is_rejected(self):
        native = [name for name in _record()["native_fields"] if name != "document_ids"]
        self.builder.build(
            records=[
                _record(
                    native_fields=native,
                    sql_derived_fields=["document_ids", "upload_id"],
                )
            ]
        )
        self.assert_error("SQL_DERIVED_FIELD_INVALID")

    def test_operator_supplied_fields_are_rejected(self):
        self.builder.build(records=[_record(operator_supplied_fields=["upload_id"])])
        self.assert_error("OPERATOR_SUPPLIED_FIELDS_REJECTED")

    def test_unknown_native_metadata_key_is_rejected(self):
        self.builder.build(
            records=[_record(native_fields=_record()["native_fields"] + ["mystery"])]
        )
        self.assert_error("NATIVE_FIELD_INVALID")

    def test_unsupported_kind_is_rejected(self):
        self.builder.build(records=[_record(kind="future_secret_kind")])
        self.assert_error("UNSUPPORTED_KIND")

    def test_missing_document_ids_is_reported(self):
        native = [name for name in _record()["native_fields"] if name != "document_ids"]

        def mutate(value):
            value["workflow_registry"][0]["document_ids"] = []
            value["workflow_registry"][0]["upload_derivation_status"] = "FAILED"
            value["workflow_registry"][0]["upload_derivation_failure_reason"] = "DOCUMENT_LINK_CONFLICT"
            value["document_registry"] = []

        self.builder.build(
            records=[
                _record(
                    document_ids=[],
                    upload_id=None,
                    native_fields=native,
                    sql_derived_fields=[],
                    validation_findings=[
                        "MISSING_DOCUMENT_IDS",
                        "UPLOAD_DERIVATION_DOCUMENT_LINK_CONFLICT",
                    ],
                )
            ],
            mutate_attestation=mutate,
        )
        self.assertIn("MISSING_DOCUMENT_IDS", self.validate()["findings"])

    def test_text_document_and_embedding_keys_are_rejected_without_echo(self):
        for key in ("document", "chunk_text", "embedding"):
            with self.subTest(key=key):
                if self.snapshot.exists():
                    self.tearDown()
                    self.setUp()
                canary = f"PROHIBITED_{key.upper()}_CANARY"
                row = _record()
                row[key] = canary
                self.builder.build(records=[row])
                stderr = StringIO()
                with redirect_stderr(stderr):
                    code = validator.main(
                        ["--fixture-only", "--snapshot", self.relative()]
                    )
                self.assertEqual(1, code)
                self.assertNotIn(canary, stderr.getvalue())
                self.assertIn("RECORD_KEYS_INVALID", stderr.getvalue())

    def test_unexpected_file_is_rejected(self):
        self.builder.build()
        with _writable_snapshot(self.snapshot):
            (self.snapshot / "unexpected-CANARY-secret.txt").write_text(
                "UNEXPECTED_FILE_CANARY", encoding="utf-8"
            )
        self.assert_error("UNEXPECTED_FILE")

    def test_raw_path_and_raw_username_are_rejected_without_echo(self):
        cases = [
            ("owner", "RAW_USERNAME_CANARY"),
            ("source_path_token", "/private/RAW_PATH_CANARY/secret.txt"),
        ]
        for field, canary in cases:
            with self.subTest(field=field):
                if self.snapshot.exists():
                    self.tearDown()
                    self.setUp()
                self.builder.build(records=[_record(**{field: canary})])
                stdout, stderr = StringIO(), StringIO()
                with redirect_stdout(stdout), redirect_stderr(stderr):
                    code = validator.main(
                        ["--fixture-only", "--snapshot", self.relative()]
                    )
                self.assertEqual(1, code)
                combined = stdout.getvalue() + stderr.getvalue()
                self.assertNotIn(canary, combined)
                self.assertNotIn("RAW_PATH_CANARY", combined)
                self.assertNotIn("RAW_USERNAME_CANARY", combined)

    def test_privacy_canary_absent_from_report_and_errors(self):
        canary = "ULTRA_SECRET_PRIVACY_CANARY_7d925"
        row = _record()
        row["credentials"] = canary
        self.builder.build(records=[row])
        stdout, stderr = StringIO(), StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = validator.main(["--fixture-only", "--snapshot", self.relative()])
        self.assertEqual(1, code)
        self.assertNotIn(canary, stdout.getvalue())
        self.assertNotIn(canary, stderr.getvalue())
        for path in self.snapshot.rglob("*"):
            self.assertNotIn(canary, path.name)

    def test_source_before_after_equality(self):
        self.builder.build()
        before = _snapshot_tree(self.snapshot)
        self.validate()
        after = _snapshot_tree(self.snapshot)
        self.assertEqual(before, after)

    def test_source_before_after_equality_on_validation_failure(self):
        self.builder.build(collections=[_collection("odysseus_rag", 2)])
        before = _snapshot_tree(self.snapshot)
        self.assert_error("RECORD_COUNT_MISMATCH")
        after = _snapshot_tree(self.snapshot)
        self.assertEqual(before, after)

    def test_no_forbidden_imports(self):
        imported_roots = {name.split(".", 1)[0].lower() for name in sys.modules}
        self.assertTrue(FORBIDDEN_MODULE_ROOTS.isdisjoint(imported_roots))
        source = VALIDATOR_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".", 1)[0])
        self.assertTrue(FORBIDDEN_MODULE_ROOTS.isdisjoint({name.lower() for name in imported}))

    def test_no_network_or_chroma_access_surface(self):
        source = VALIDATOR_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported = set()
        calls = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
            elif isinstance(node, ast.Call):
                if isinstance(node.func, ast.Attribute):
                    calls.add(node.func.attr)
                elif isinstance(node.func, ast.Name):
                    calls.add(node.func.id)
        self.assertFalse(any(name.startswith(("socket", "http", "urllib")) for name in imported))
        self.assertTrue({"connect", "urlopen", "request", "sqlite3"}.isdisjoint(calls))
        self.builder.build()
        self.validate()

    def test_report_invariants_are_always_fail_closed(self):
        self.builder.build()
        report = self.validate()
        self.assertEqual("OBSERVED_PHYSICAL_METADATA_ROWS_ONLY", report["proof_scope"])
        self.assertFalse(report["logical_chunk_completeness"])
        self.assertFalse(report["real_source_capture_authorized"])
        self.assertFalse(report["real_chroma_access_performed"])
        self.assertFalse(report["complete"])
        self.assertFalse(report["approved"])

        with _writable_snapshot(self.snapshot):
            (self.snapshot / "records.jsonl").write_text("BROKEN_CANARY\n", encoding="utf-8")
        stdout, stderr = StringIO(), StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = validator.main(["--fixture-only", "--snapshot", self.relative()])
        self.assertEqual(1, code)
        error = json.loads(stderr.getvalue())
        self.assertFalse(error["logical_chunk_completeness"])
        self.assertFalse(error["real_source_capture_authorized"])
        self.assertFalse(error["real_chroma_access_performed"])
        self.assertFalse(error["complete"])
        self.assertFalse(error["approved"])
        self.assertFalse(error["valid"])

    def test_empty_declared_collection_is_explicit_and_valid(self):
        self.builder.build(records=[], collections=[_collection("odysseus_rag", 0)])
        report = self.validate()
        self.assertEqual(1, report["empty_collection_count"])

    def test_capture_status_must_be_fixture_only(self):
        bad = _collection("odysseus_rag", 1)
        bad["capture_status"] = "COMPLETE_REAL_SOURCE"
        self.builder.build(collections=[bad])
        self.assert_error("CAPTURE_STATUS_INVALID")

    def test_boolean_schema_versions_are_rejected(self):
        with self.subTest(field="manifest_version"):
            self.builder.build(
                mutate_manifest=lambda value: value.update(manifest_version=True)
            )
            self.assert_error("MANIFEST_CONTRACT_MISMATCH")

        self.tearDown()
        self.setUp()
        with self.subTest(field="attestation_version"):
            self.builder.build(
                mutate_attestation=lambda value: value.update(attestation_version=True)
            )
            self.assert_error("CAPTURE_ATTESTATION_INVALID")

    def test_provenance_categories_must_be_disjoint_and_complete(self):
        overlap = _record(sql_derived_fields=["owner", "upload_id"])
        self.builder.build(records=[overlap])
        self.assert_error("PROVENANCE_OVERLAP")

    def test_missing_provenance_for_populated_value(self):
        native = [name for name in _record()["native_fields"] if name != "owner"]
        self.builder.build(records=[_record(native_fields=native)])
        self.assert_error("PROVENANCE_MISSING")

    def test_manifest_digest_and_fixture_attestation_are_enforced(self):
        self.builder.build()
        with _writable_snapshot(self.snapshot):
            path = self.snapshot / "snapshot-manifest.json"
            manifest = json.loads(path.read_text(encoding="utf-8"))
            manifest["canonical_sha256"] = "0" * 64
            path.write_bytes(_json_bytes(manifest))
        self.assert_error("MANIFEST_CANONICAL_DIGEST_MISMATCH")

    def test_attestation_tampering_cannot_be_resealed_without_launcher_secret(self):
        self.builder.build()
        with _writable_snapshot(self.snapshot):
            attestation_path = self.snapshot / "capture-attestation.json"
            attestation = json.loads(attestation_path.read_text(encoding="utf-8"))
            attestation["workflow_registry"][0]["owner"] = OWNER_B
            attestation_bytes = _json_bytes(attestation)
            attestation_path.write_bytes(attestation_bytes)

            manifest_path = self.snapshot / "snapshot-manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            for entry in manifest["files"]:
                if entry["path"] == "capture-attestation.json":
                    entry.update(_file_entry("capture-attestation.json", attestation_bytes))
            manifest["tree_sha256"] = _tree_digest(manifest["files"])
            manifest["canonical_sha256"] = _canonical_digest(manifest)
            manifest_path.write_bytes(_json_bytes(manifest))
        self.assert_error("FIXTURE_ATTESTATION_INVALID")

    def test_snapshot_files_must_be_read_only(self):
        self.builder.build()
        self.snapshot.chmod(0o700)
        (self.snapshot / "records.jsonl").chmod(0o600)
        self.snapshot.chmod(0o500)
        self.assert_error("SNAPSHOT_NOT_READ_ONLY")

    def test_absolute_and_traversal_paths_are_rejected(self):
        self.builder.build()
        for value in (str(self.snapshot), "snapshots/../outside"):
            with self.subTest(value=value):
                with self.assertRaises(validator.SnapshotError) as caught:
                    validator.validate_fixture_snapshot(value, fixture_only=True)
                self.assertEqual("SNAPSHOT_PATH_INVALID", caught.exception.code)

    def test_symlink_and_hard_link_snapshot_files_are_rejected(self):
        self.builder.build()
        with _writable_snapshot(self.snapshot):
            records = self.snapshot / "records.jsonl"
            saved = records.read_bytes()
            records.unlink()
            records.symlink_to("collections.json")
        self.assert_error("SYMLINK_REJECTED")

        self.tearDown()
        self.setUp()
        self.builder.build()
        with _writable_snapshot(self.snapshot):
            records = self.snapshot / "records.jsonl"
            records.unlink()
            os.link(self.snapshot / "collections.json", records)
        self.assert_error("HARD_LINK_REJECTED")

    def test_contract_canonical_digest(self):
        self.assertEqual(CONTRACT_SHA256, _canonical_digest(CONTRACT))

    def test_owner_registry_collision_is_rejected(self):
        def mutate(value):
            value["owner_registry"]["retired"].append(OWNER_A)

        self.builder.build(mutate_attestation=mutate)
        self.assert_error("OWNER_REGISTRY_COLLISION")

    def test_referenced_registry_identifier_collisions_are_rejected(self):
        cases = (
            (
                "workflow",
                "WORKFLOW_REGISTRY_COLLISION",
                lambda value: value["workflow_registry"].append(
                    {
                        **copy.deepcopy(value["workflow_registry"][0]),
                        "workflow_type": "video",
                    }
                ),
            ),
            (
                "document",
                "DOCUMENT_REGISTRY_COLLISION",
                lambda value: value["document_registry"].append(
                    copy.deepcopy(value["document_registry"][0])
                ),
            ),
            (
                "upload",
                "UPLOAD_REGISTRY_COLLISION",
                lambda value: value["upload_registry"].append(
                    copy.deepcopy(value["upload_registry"][0])
                ),
            ),
        )
        for label, expected, mutate in cases:
            with self.subTest(registry=label):
                if self.snapshot.exists():
                    self.tearDown()
                    self.setUp()
                self.builder.build(mutate_attestation=mutate)
                self.assert_error(expected)

    def test_unreferenced_registry_identifier_collisions_are_rejected(self):
        unused_workflow = "workflow_" + "7" * 32
        unused_document = "document_" + "8" * 32
        unused_upload = "upload_" + "9" * 32

        def duplicate_workflow(value):
            row = copy.deepcopy(value["workflow_registry"][0])
            row["workflow_id"] = unused_workflow
            value["workflow_registry"].extend([row, copy.deepcopy(row)])

        def duplicate_document(value):
            row = {"document_id": unused_document, "owner": OWNER_A}
            value["document_registry"].extend([row, copy.deepcopy(row)])

        def duplicate_upload(value):
            row = {"upload_id": unused_upload, "owner": OWNER_A}
            value["upload_registry"].extend([row, copy.deepcopy(row)])

        cases = (
            ("workflow", "WORKFLOW_REGISTRY_COLLISION", duplicate_workflow),
            ("document", "DOCUMENT_REGISTRY_COLLISION", duplicate_document),
            ("upload", "UPLOAD_REGISTRY_COLLISION", duplicate_upload),
        )
        for label, expected, mutate in cases:
            with self.subTest(registry=label):
                if self.snapshot.exists():
                    self.tearDown()
                    self.setUp()
                self.builder.build(mutate_attestation=mutate)
                self.assert_error(expected)


if __name__ == "__main__":
    unittest.main()
