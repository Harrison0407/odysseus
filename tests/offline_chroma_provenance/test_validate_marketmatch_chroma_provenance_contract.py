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
import shutil
import stat
import sys
import unittest
from unittest import mock
import uuid


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
VALIDATOR_PATH = (
    REPOSITORY_ROOT
    / "scripts"
    / "validate_marketmatch_chroma_provenance_contract.py"
)
CONTRACT_PATH = (
    REPOSITORY_ROOT
    / "scripts"
    / "offline_package_contracts"
    / "chroma_version_deployment_provenance_v1.json"
)
LAUNCHER_PATH = REPOSITORY_ROOT / "tests" / "run_offline_chroma_provenance_tests.sh"
CONTRACT = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
CONTRACT_SHA256 = CONTRACT["canonical_sha256"]
SENTINEL = os.environ["MARKETMATCH_CHROMA_PROVENANCE_SENTINEL"]
TEST_ROOT = Path(os.environ["MARKETMATCH_CHROMA_PROVENANCE_TEST_ROOT"])

FORBIDDEN_MODULE_ROOTS = {
    "app",
    "chromadb",
    "core",
    "docker",
    "requests",
    "routes",
    "services",
    "sqlalchemy",
    "src",
    "workers",
}
REPORT_INVARIANTS = {
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
ALL_STATUS_CODES = {
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
ZERO_SHA256 = "0" * 64


def _load_validator():
    before = set(sys.modules)
    spec = importlib.util.spec_from_file_location(
        "marketmatch_chroma_provenance_validator_under_test", VALIDATOR_PATH
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


def _value_digest(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _json_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False).encode(
        "utf-8"
    ) + b"\n"


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _file_record(name: str, payload: bytes) -> dict:
    return {"path": name, "size": len(payload), "sha256": _sha256(payload)}


def _collection(name: str = "odysseus_rag") -> dict:
    mapping = CONTRACT["collection_contract"]["physical_collections"][name]
    return {
        "collection_name": name,
        "family": mapping["family"],
        "lane": mapping["lane"],
        "collection_uuid_status": "SYNTHETIC_UUID_ATTESTED",
        "tenant_database_binding_status": "EXPLICIT_SYNTHETIC_BINDING",
        "record_count": 3,
        "sanitized_metadata_digest": hashlib.sha256(
            (name + ":sanitized-metadata").encode("ascii")
        ).hexdigest(),
        "embedding_fingerprint_status": "SYNTHETIC_DIGEST_ATTESTED",
        "embedding_dimension_status": "SYNTHETIC_DIMENSION_ATTESTED",
        "distance_metric_status": "COSINE_METADATA_ATTESTED",
        "snapshot_identifier": "synthetic_snapshot_" + "5" * 32,
    }


def _groups() -> dict:
    items = [_collection()]
    inventory = _value_digest(items)
    return {
        "contract": {
            "contract_name": CONTRACT["contract_name"],
            "contract_version": 1,
            "contract_sha256": CONTRACT_SHA256,
            "upstream_commit": "9844a2f9a1996b8c8135a9e7bbde6a72f41df5ed",
            "marketmatch_checkpoint": "6197984ccec995b630006253cf6eb62c43908d5f",
            "integration_baseline": "966ffcb30765755e3378a63f60f201f940a87daf",
            "supported_combination_count": 0,
            "compatibility_allowlist_empty": True,
            "real_capture_authorized": False,
            "remote_capture_supported": False,
            "unknown_defaults_permitted": False,
        },
        "source_code": {
            "repository_commit": "966ffcb30765755e3378a63f60f201f940a87daf",
            "application_deployment_commit": "966ffcb30765755e3378a63f60f201f940a87daf",
            "capture_adapter_commit": "966ffcb30765755e3378a63f60f201f940a87daf",
            "source_classification": "SYNTHETIC_DISPOSABLE_FIXTURE",
        },
        "client": {
            "distribution_name": "chromadb-client",
            "exact_version": "1.0.0",
            "artifact_sha256": hashlib.sha256(b"synthetic-client-artifact").hexdigest(),
            "installed_tree_or_record_sha256": hashlib.sha256(
                b"synthetic-client-record"
            ).hexdigest(),
            "dependency_lock_sha256": hashlib.sha256(
                b"synthetic-dependency-closure"
            ).hexdigest(),
            "python_abi": "py3-none-any",
            "platform": "synthetic_platform",
        },
        "server": {
            "source_type": "synthetic_server",
            "exact_build": "synthetic-build-1.0.0",
            "image_tag": "synthetic-1.0.0",
            "image_digest": hashlib.sha256(b"synthetic-image").hexdigest(),
            "platform_digest": hashlib.sha256(b"synthetic-platform").hexdigest(),
            "binary_or_package_sha256": hashlib.sha256(
                b"synthetic-server-package"
            ).hexdigest(),
        },
        "connection_identity": {
            "deployment_mode": "synthetic_fixture",
            "tenant": "synthetic_tenant_" + "1" * 32,
            "database": "synthetic_database_" + "2" * 32,
            "verification_method": "SYNTHETIC_LAUNCHER_ATTESTATION",
            "host_classification": "SYNTHETIC_ISOLATED",
            "host_alias": "synthetic_host_" + "3" * 32,
            "authentication_mode": "NONE_SYNTHETIC",
            "tls_mode": "NOT_APPLICABLE_SYNTHETIC",
            "header_names": [],
        },
        "persistence": {
            "opaque_store_identifier": "synthetic_store_" + "4" * 32,
            "snapshot_identifier": "synthetic_snapshot_" + "5" * 32,
            "acquisition_mechanism": "SYNTHETIC_FIXTURE_BUILD",
            "complete_tree_digest": hashlib.sha256(b"synthetic-store-tree").hexdigest(),
            "file_family_policy": "SYNTHETIC_CLOSED_FOUR_FILE_SET",
            "source_ownership_class": "SYNTHETIC_LAUNCHER",
            "source_read_only": True,
            "before_after_equal": True,
        },
        "collections": {
            "inventory_before_sha256": inventory,
            "inventory_after_sha256": inventory,
            "before_after_equal": True,
            "items": items,
        },
        "quiescence": {
            "evidence_status": "SYNTHETIC_QUIESCENCE_ATTESTED",
            "evidence_sha256": hashlib.sha256(b"synthetic-quiescence").hexdigest(),
            "application_stopped": True,
            "server_stopped": True,
            "writers_stopped": True,
            "inventory_before_after_equal": True,
            "acquisition_window_start": "2026-07-16T12:00:00Z",
            "acquisition_window_end": "2026-07-16T12:00:01Z",
        },
        "privacy": {
            "policy_name": "SYNTHETIC_FIXED_CODE_PRIVACY_V1",
            "policy_sha256": hashlib.sha256(b"synthetic-privacy-policy").hexdigest(),
            "raw_values_retained": False,
            "prohibited_values_present": False,
            "tenant_database_values_are_aliases": True,
            "report_fixed_codes_only": True,
        },
        "adapter": {
            "adapter_name": "SYNTHETIC_PROVENANCE_CONTRACT_VALIDATOR",
            "adapter_commit": "966ffcb30765755e3378a63f60f201f940a87daf",
            "adapter_contract_sha256": CONTRACT_SHA256,
            "standard_library_only": True,
            "network_capability": False,
            "runtime_discovery_capability": False,
            "application_import_capability": False,
            "mutation_capability": False,
        },
        "review": {
            "attestation_policy": "SYNTHETIC_PLACEHOLDERS_ONLY",
            "operator_role": "OPERATOR",
            "reviewer_role": "INDEPENDENT_REVIEWER",
            "aliases_distinct": True,
            "self_approval": False,
            "decision_approved": False,
            "detached_signatures_real": False,
        },
        "decision": {
            "requested_outcome": "BLOCKED",
            "claimed_blockers": ["UNKNOWN_OR_UNSUPPORTED"],
            "client_server_compatibility": "UNREVIEWED",
            "real_capture_authorized": False,
            "remote_capture_supported": False,
            "supported_combination_count": 0,
            "compatibility_allowlist_empty": True,
        },
    }


class FixtureBuilder:
    def __init__(self, directory: Path):
        self.directory = directory

    def build(
        self,
        *,
        mutate_groups=None,
        mutate_manifest=None,
        mutate_subject=None,
        mutate_operator=None,
        mutate_reviewer=None,
    ) -> Path:
        self.directory.mkdir(mode=0o700)
        fixture_id = "fixture_" + uuid.uuid4().hex
        groups = _groups()
        if mutate_groups:
            mutate_groups(groups)

        subject = {
            "subject_version": 1,
            "fixture_id": fixture_id,
            "contract_sha256": CONTRACT_SHA256,
            "contract_group_sha256": _value_digest(groups["contract"]),
            "source_code_sha256": _value_digest(groups["source_code"]),
            "client_sha256": _value_digest(groups["client"]),
            "server_sha256": _value_digest(groups["server"]),
            "connection_identity_sha256": _value_digest(groups["connection_identity"]),
            "persistence_sha256": _value_digest(groups["persistence"]),
            "collections_sha256": _value_digest(groups["collections"]),
            "quiescence_sha256": _value_digest(groups["quiescence"]),
            "privacy_sha256": _value_digest(groups["privacy"]),
            "adapter_sha256": _value_digest(groups["adapter"]),
            "review_sha256": _value_digest(groups["review"]),
            "decision_sha256": _value_digest(groups["decision"]),
            "canonical_sha256": ZERO_SHA256,
        }
        if mutate_subject:
            mutate_subject(subject)
        subject["canonical_sha256"] = _canonical_digest(subject)

        operator = self._attestation(
            fixture_id,
            subject["canonical_sha256"],
            "synthetic_operator_" + "6" * 32,
            "OPERATOR",
        )
        reviewer = self._attestation(
            fixture_id,
            subject["canonical_sha256"],
            "synthetic_reviewer_" + "7" * 32,
            "INDEPENDENT_REVIEWER",
        )
        if mutate_operator:
            mutate_operator(operator)
            operator["canonical_sha256"] = _canonical_digest(operator)
        if mutate_reviewer:
            mutate_reviewer(reviewer)
            reviewer["canonical_sha256"] = _canonical_digest(reviewer)

        payloads = {
            "compatibility-subject.json": _json_bytes(subject),
            "operator-attestation.json": _json_bytes(operator),
            "reviewer-attestation.json": _json_bytes(reviewer),
        }
        entries = [_file_record(name, payload) for name, payload in sorted(payloads.items())]
        manifest = {
            "manifest_version": 1,
            "contract_name": CONTRACT["contract_name"],
            "contract_sha256": CONTRACT_SHA256,
            "fixture_provenance": "SYNTHETIC_DISPOSABLE_FIXTURE",
            "fixture_id": fixture_id,
            "sealed": True,
            **groups,
            "files": entries,
            "tree_sha256": _value_digest(entries),
            "launcher_attestation": ZERO_SHA256,
            "canonical_sha256": ZERO_SHA256,
        }
        if mutate_manifest:
            mutate_manifest(manifest)
        proof = (
            "marketmatch-chroma-provenance-v1\0"
            + fixture_id
            + "\0"
            + CONTRACT_SHA256
            + "\0"
            + manifest["tree_sha256"]
            + "\0"
            + _sha256(payloads["compatibility-subject.json"])
            + "\0"
            + _value_digest({name: manifest[name] for name in validator.PROVENANCE_GROUPS})
        ).encode("utf-8")
        manifest["launcher_attestation"] = hmac.new(
            SENTINEL.encode("ascii"), proof, hashlib.sha256
        ).hexdigest()
        manifest["canonical_sha256"] = _canonical_digest(manifest)
        payloads["provenance-manifest.json"] = _json_bytes(manifest)

        for name, payload in payloads.items():
            path = self.directory / name
            path.write_bytes(payload)
            path.chmod(0o400)
        self.directory.chmod(0o500)
        return self.directory

    @staticmethod
    def _attestation(
        fixture_id: str, subject_sha256: str, alias: str, role: str
    ) -> dict:
        value = {
            "attestation_version": 1,
            "fixture_id": fixture_id,
            "subject_sha256": subject_sha256,
            "actor_alias": alias,
            "role": role,
            "timestamp": "2026-07-16T12:00:02Z",
            "detached_signature_status": "SYNTHETIC_PLACEHOLDER_ONLY",
            "decision_binding_status": "BOUND_NOT_APPROVED",
            "canonical_sha256": ZERO_SHA256,
        }
        value["canonical_sha256"] = _canonical_digest(value)
        return value


@contextmanager
def _writable_fixture(path: Path):
    path.chmod(0o700)
    for child in path.iterdir():
        if not child.is_symlink():
            child.chmod(0o600)
    try:
        yield
    finally:
        if path.exists():
            for child in path.iterdir():
                if not child.is_symlink():
                    child.chmod(0o400)
            path.chmod(0o500)


def _filesystem_snapshot(path: Path) -> tuple:
    root = path.stat(follow_symlinks=False)
    rows = []
    for child in sorted(path.iterdir()):
        metadata = child.stat(follow_symlinks=False)
        payload = child.read_bytes() if stat.S_ISREG(metadata.st_mode) else b""
        rows.append(
            (
                child.name,
                metadata.st_dev,
                metadata.st_ino,
                metadata.st_size,
                stat.S_IMODE(metadata.st_mode),
                metadata.st_nlink,
                _sha256(payload),
            )
        )
    return (
        root.st_dev,
        root.st_ino,
        root.st_size,
        stat.S_IMODE(root.st_mode),
        root.st_nlink,
        tuple(rows),
    )


class ProvenanceContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture_path = TEST_ROOT / "fixtures" / ("fixture-" + uuid.uuid4().hex)
        self.builder = FixtureBuilder(self.fixture_path)

    def tearDown(self) -> None:
        if self.fixture_path.exists() or self.fixture_path.is_symlink():
            if self.fixture_path.is_symlink():
                self.fixture_path.unlink()
            else:
                self.fixture_path.chmod(0o700)
                for child in self.fixture_path.iterdir():
                    if child.is_symlink():
                        child.unlink()
                    else:
                        child.chmod(0o600)
                shutil.rmtree(self.fixture_path)

    def validate(self, path: Path | None = None) -> dict:
        chosen = path or self.fixture_path
        relative = chosen.relative_to(TEST_ROOT)
        return validator.validate_fixture_contract(str(relative), fixture_only=True)

    def assert_error(self, code: str, *, path: Path | None = None) -> None:
        with self.assertRaises(validator.ContractError) as raised:
            self.validate(path)
        self.assertEqual(raised.exception.code, code)

    def build_with(self, group: str, key: str, value) -> Path:
        return self.builder.build(
            mutate_groups=lambda groups: groups[group].__setitem__(key, value)
        )

    def test_valid_synthetic_provenance_fixture_remains_blocked(self) -> None:
        self.builder.build()
        result = self.validate()
        self.assertTrue(result["valid"])
        self.assertEqual(result["status"], "VALID_SYNTHETIC_PROVENANCE_BLOCKED")
        self.assertEqual(result["blockers"], ["UNKNOWN_OR_UNSUPPORTED"])
        for key, value in REPORT_INVARIANTS.items():
            self.assertIs(result[key], value) if isinstance(value, bool) else self.assertEqual(result[key], value)

    def test_empty_compatibility_allowlist_is_contractual(self) -> None:
        self.assertEqual(CONTRACT["supported_combinations"], [])
        self.assertEqual(CONTRACT["invariants"]["supported_combination_count"], 0)
        self.assertTrue(CONTRACT["invariants"]["compatibility_allowlist_empty"])

    def test_supported_exact_is_unreachable(self) -> None:
        self.builder.build()
        result = self.validate()
        self.assertNotIn("SUPPORTED_EXACT", result["blockers"])
        self.assertFalse(result["real_capture_authorized"])

    def test_supported_reviewed_variant_is_unreachable(self) -> None:
        self.builder.build()
        result = self.validate()
        self.assertNotIn("SUPPORTED_REVIEWED_VARIANT", result["blockers"])
        self.assertFalse(result["real_capture_authorized"])

    def test_unpinned_client_version(self) -> None:
        self.build_with("client", "exact_version", ">=1.0")
        result = self.validate()
        self.assertIn("UNPINNED_CLIENT", result["blockers"])

    def test_missing_client_artifact_hash(self) -> None:
        self.build_with("client", "artifact_sha256", ZERO_SHA256)
        self.assertIn("UNPINNED_CLIENT", self.validate()["blockers"])

    def test_unsupported_client_distribution(self) -> None:
        self.build_with("client", "distribution_name", "synthetic-client")
        result = self.validate()
        self.assertFalse(result["valid"])
        self.assertIn("UNKNOWN_OR_UNSUPPORTED", result["blockers"])

    def test_missing_dependency_closure(self) -> None:
        self.build_with("client", "dependency_lock_sha256", "")
        self.assertIn("UNPINNED_CLIENT", self.validate()["blockers"])

    def test_moving_server_tag(self) -> None:
        self.build_with("server", "image_tag", "latest")
        self.assertIn("UNPINNED_SERVER", self.validate()["blockers"])

    def test_missing_image_digest(self) -> None:
        self.build_with("server", "image_digest", "")
        self.assertIn("UNPINNED_SERVER", self.validate()["blockers"])

    def test_missing_platform_digest(self) -> None:
        self.build_with("server", "platform_digest", ZERO_SHA256)
        self.assertIn("UNPINNED_SERVER", self.validate()["blockers"])

    def test_moving_tag_is_rejected_for_native_source_too(self) -> None:
        def mutate(groups):
            groups["server"]["source_type"] = "native_binary"
            groups["server"]["image_tag"] = "latest"

        self.builder.build(mutate_groups=mutate)
        self.assertIn("UNPINNED_SERVER", self.validate()["blockers"])

    def test_unknown_server_build(self) -> None:
        self.build_with("server", "exact_build", "unknown")
        self.assertIn("UNKNOWN_SERVER_BUILD", self.validate()["blockers"])

    def test_client_server_mismatch(self) -> None:
        self.build_with("decision", "client_server_compatibility", "MISMATCH")
        self.assertIn("CLIENT_SERVER_MISMATCH", self.validate()["blockers"])

    def test_missing_tenant(self) -> None:
        self.build_with("connection_identity", "tenant", "")
        self.assertIn("UNKNOWN_TENANT_DATABASE", self.validate()["blockers"])

    def test_missing_database(self) -> None:
        self.build_with("connection_identity", "database", "")
        self.assertIn("UNKNOWN_TENANT_DATABASE", self.validate()["blockers"])

    def test_implicit_and_default_tenant_are_rejected(self) -> None:
        for value in ("implicit", "default", "client_default", "server_default"):
            with self.subTest(value=value):
                path = TEST_ROOT / "fixtures" / ("fixture-" + uuid.uuid4().hex)
                FixtureBuilder(path).build(
                    mutate_groups=lambda groups, value=value: groups[
                        "connection_identity"
                    ].__setitem__("tenant", value)
                )
                try:
                    result = self.validate(path)
                    self.assertIn("UNKNOWN_TENANT_DATABASE", result["blockers"])
                finally:
                    path.chmod(0o700)
                    for child in path.iterdir():
                        child.chmod(0o600)
                    shutil.rmtree(path)

    def test_unsupported_deployment_mode(self) -> None:
        self.build_with("connection_identity", "deployment_mode", "native_local_server")
        self.assertIn("UNSUPPORTED_DEPLOYMENT_MODE", self.validate()["blockers"])

    def test_remote_deployment_is_always_blocked(self) -> None:
        self.build_with("connection_identity", "deployment_mode", "remote_server")
        blockers = self.validate()["blockers"]
        self.assertIn("REMOTE_CAPTURE_UNSUPPORTED", blockers)
        self.assertIn("UNSUPPORTED_DEPLOYMENT_MODE", blockers)

    def test_missing_persistence_identifier(self) -> None:
        self.build_with("persistence", "opaque_store_identifier", "")
        self.assertIn("PERSISTENCE_PROVENANCE_MISSING", self.validate()["blockers"])

    def test_missing_snapshot_identifier(self) -> None:
        self.build_with("persistence", "snapshot_identifier", "")
        self.assertIn("PERSISTENCE_PROVENANCE_MISSING", self.validate()["blockers"])

    def test_persistence_before_after_false(self) -> None:
        self.build_with("persistence", "before_after_equal", False)
        self.assertIn("PERSISTENCE_PROVENANCE_MISSING", self.validate()["blockers"])

    def test_missing_quiescence_evidence(self) -> None:
        self.build_with("quiescence", "evidence_sha256", "")
        self.assertIn("PERSISTENCE_PROVENANCE_MISSING", self.validate()["blockers"])

    def test_unknown_collection(self) -> None:
        self.builder.build(
            mutate_groups=lambda groups: groups["collections"]["items"][0].__setitem__(
                "collection_name", "unknown_collection"
            )
        )
        result = self.validate()
        self.assertFalse(result["valid"])
        self.assertIn("UNKNOWN_OR_UNSUPPORTED", result["blockers"])

    def test_invalid_collection_family_and_lane(self) -> None:
        def mutate(groups):
            item = groups["collections"]["items"][0]
            item["family"] = "memory"
            item["lane"] = "custom"

        self.builder.build(mutate_groups=mutate)
        self.assertFalse(self.validate()["valid"])

    def test_collection_tenant_database_binding_failure(self) -> None:
        self.builder.build(
            mutate_groups=lambda groups: groups["collections"]["items"][0].__setitem__(
                "tenant_database_binding_status", "UNBOUND"
            )
        )
        self.assertIn("UNKNOWN_TENANT_DATABASE", self.validate()["blockers"])

    def test_collection_snapshot_mismatch(self) -> None:
        self.builder.build(
            mutate_groups=lambda groups: groups["collections"]["items"][0].__setitem__(
                "snapshot_identifier", "synthetic_snapshot_" + "8" * 32
            )
        )
        self.assertIn("PERSISTENCE_PROVENANCE_MISSING", self.validate()["blockers"])

    def test_missing_collection_digest(self) -> None:
        self.builder.build(
            mutate_groups=lambda groups: groups["collections"]["items"][0].__setitem__(
                "sanitized_metadata_digest", ""
            )
        )
        self.assertIn("PERSISTENCE_PROVENANCE_MISSING", self.validate()["blockers"])

    def test_collection_inventory_digest_mismatch(self) -> None:
        self.build_with("collections", "inventory_before_sha256", "a1" * 32)
        self.assertIn("PERSISTENCE_PROVENANCE_MISSING", self.validate()["blockers"])

    def test_operator_reviewer_alias_collision(self) -> None:
        self.builder.build(
            mutate_reviewer=lambda row: row.__setitem__(
                "actor_alias", "synthetic_operator_" + "6" * 32
            )
        )
        self.assert_error("INVALID_ATTESTATION")

    def test_attestation_subject_mismatch(self) -> None:
        self.builder.build(
            mutate_reviewer=lambda row: row.__setitem__("subject_sha256", "9" * 64)
        )
        self.assert_error("INVALID_ATTESTATION")

    def test_invalid_attestation_role(self) -> None:
        self.builder.build(
            mutate_operator=lambda row: row.__setitem__("role", "REVIEWER")
        )
        self.assert_error("INVALID_ATTESTATION")

    def test_invalid_attestation_timestamp(self) -> None:
        self.builder.build(
            mutate_operator=lambda row: row.__setitem__("timestamp", "not-a-timestamp")
        )
        self.assert_error("INVALID_ATTESTATION")

    def test_non_padded_attestation_timestamp_shape_is_invalid(self) -> None:
        self.builder.build(
            mutate_operator=lambda row: row.__setitem__("timestamp", "2026-7-16T1:02:03Z")
        )
        self.assert_error("INVALID_ATTESTATION")

    def test_self_approval_and_unbound_approval_are_invalid(self) -> None:
        cases = (
            ("self-approval", {"group": "review", "key": "self_approval", "value": True}),
            ("decision-approved", {"group": "review", "key": "decision_approved", "value": True}),
        )
        for label, change in cases:
            with self.subTest(label=label):
                path = TEST_ROOT / "fixtures" / ("fixture-" + uuid.uuid4().hex)
                FixtureBuilder(path).build(
                    mutate_groups=lambda groups, change=change: groups[
                        change["group"]
                    ].__setitem__(change["key"], change["value"])
                )
                try:
                    with self.assertRaises(validator.ContractError) as raised:
                        self.validate(path)
                    self.assertEqual(raised.exception.code, "INVALID_ATTESTATION")
                finally:
                    path.chmod(0o700)
                    for child in path.iterdir():
                        child.chmod(0o600)
                    shutil.rmtree(path)

        self.builder.build(
            mutate_operator=lambda row: row.__setitem__(
                "decision_binding_status", "UNBOUND_APPROVED"
            )
        )
        self.assert_error("INVALID_ATTESTATION")

    def test_unrecognized_attestation_key_is_invalid(self) -> None:
        self.builder.build(
            mutate_operator=lambda row: row.__setitem__("extra_claim", False)
        )
        self.assert_error("INVALID_ATTESTATION")

    def test_unknown_manifest_key(self) -> None:
        self.builder.build(mutate_manifest=lambda manifest: manifest.__setitem__("extra", False))
        self.assert_error("READ_ERROR")

    def test_duplicate_json_key_is_rejected(self) -> None:
        self.builder.build()
        with _writable_fixture(self.fixture_path):
            path = self.fixture_path / "provenance-manifest.json"
            payload = path.read_text(encoding="utf-8").replace(
                '"manifest_version": 1,',
                '"manifest_version": 1,\n  "manifest_version": 1,',
                1,
            )
            path.write_text(payload, encoding="utf-8")
        self.assert_error("READ_ERROR")

    def test_non_rfc8259_number_is_rejected(self) -> None:
        self.builder.build()
        with _writable_fixture(self.fixture_path):
            path = self.fixture_path / "provenance-manifest.json"
            payload = path.read_text(encoding="utf-8").replace(
                '"record_count": 3', '"record_count": NaN', 1
            )
            path.write_text(payload, encoding="utf-8")
        self.assert_error("READ_ERROR")

    def test_unexpected_file(self) -> None:
        self.builder.build()
        with _writable_fixture(self.fixture_path):
            extra = self.fixture_path / "unexpected.json"
            extra.write_text("{}\n", encoding="utf-8")
            extra.chmod(0o400)
        self.assert_error("READ_ERROR")

    def test_symlink_and_hard_link_rejection(self) -> None:
        for link_kind in ("symlink", "hardlink"):
            with self.subTest(link_kind=link_kind):
                path = TEST_ROOT / "fixtures" / ("fixture-" + uuid.uuid4().hex)
                FixtureBuilder(path).build()
                path.chmod(0o700)
                victim = path / "reviewer-attestation.json"
                victim.chmod(0o600)
                victim.unlink()
                target = path / "operator-attestation.json"
                if link_kind == "symlink":
                    victim.symlink_to(target.name)
                else:
                    os.link(target, victim)
                path.chmod(0o500)
                try:
                    self.assert_error("READ_ERROR", path=path)
                finally:
                    path.chmod(0o700)
                    victim.unlink()
                    for child in path.iterdir():
                        child.chmod(0o600)
                    shutil.rmtree(path)

    def test_absolute_path_rejection(self) -> None:
        canary = "/private/phase3g-absolute-canary"
        self.build_with("client", "platform", canary)
        self.assert_error("PRIVACY_POLICY_VIOLATION")

    def test_url_rejection(self) -> None:
        self.build_with("client", "platform", "https://phase3g.invalid/resource")
        self.assert_error("PRIVACY_POLICY_VIOLATION")

    def test_hostname_ip_and_email_rejection(self) -> None:
        for value in ("host.phase3g.invalid", "192.0.2.10", "actor@phase3g.invalid"):
            with self.subTest(value=value):
                path = TEST_ROOT / "fixtures" / ("fixture-" + uuid.uuid4().hex)
                FixtureBuilder(path).build(
                    mutate_groups=lambda groups, value=value: groups["client"].__setitem__(
                        "platform", value
                    )
                )
                try:
                    with self.assertRaises(validator.ContractError) as raised:
                        self.validate(path)
                    self.assertEqual(raised.exception.code, "PRIVACY_POLICY_VIOLATION")
                finally:
                    path.chmod(0o700)
                    for child in path.iterdir():
                        child.chmod(0o600)
                    shutil.rmtree(path)

    def test_windows_unc_ipv6_container_and_cloud_identifier_rejection(self) -> None:
        values = (
            "C:\\phase3g\\private",
            "\\\\phase3g-server\\private-share",
            "2001:db8::1234",
            "a" * 64,
            "arn:aws:s3:::phase3g-private-bucket",
        )
        for value in values:
            with self.subTest(value=value):
                path = TEST_ROOT / "fixtures" / ("fixture-" + uuid.uuid4().hex)
                FixtureBuilder(path).build(
                    mutate_groups=lambda groups, value=value: groups["client"].__setitem__(
                        "platform", value
                    )
                )
                try:
                    with self.assertRaises(validator.ContractError) as raised:
                        self.validate(path)
                    self.assertEqual(raised.exception.code, "PRIVACY_POLICY_VIOLATION")
                finally:
                    path.chmod(0o700)
                    for child in path.iterdir():
                        child.chmod(0o600)
                    shutil.rmtree(path)

    def test_credential_and_token_canary_rejection(self) -> None:
        self.builder.build(
            mutate_groups=lambda groups: groups["client"].__setitem__(
                "platform", "bearer phase3g-token-canary"
            )
        )
        self.assert_error("PRIVACY_POLICY_VIOLATION")

    def test_embedding_url_key_rejection(self) -> None:
        self.builder.build(
            mutate_groups=lambda groups: groups["client"].__setitem__(
                "embedding_url", "synthetic-redacted"
            )
        )
        self.assert_error("PRIVACY_POLICY_VIOLATION")

    def test_raw_identity_volume_document_and_embedding_keys_are_rejected(self) -> None:
        for key in (
            "username",
            "volume_name",
            "collection_metadata",
            "document_text",
            "embeddings",
        ):
            with self.subTest(key=key):
                path = TEST_ROOT / "fixtures" / ("fixture-" + uuid.uuid4().hex)
                FixtureBuilder(path).build(
                    mutate_groups=lambda groups, key=key: groups["client"].__setitem__(
                        key, "synthetic-redacted"
                    )
                )
                try:
                    with self.assertRaises(validator.ContractError) as raised:
                        self.validate(path)
                    self.assertEqual(raised.exception.code, "PRIVACY_POLICY_VIOLATION")
                finally:
                    path.chmod(0o700)
                    for child in path.iterdir():
                        child.chmod(0o600)
                    shutil.rmtree(path)

    def test_privacy_canary_is_absent_from_stdout_stderr_and_report(self) -> None:
        canary = "bearer phase3g-private-canary"
        self.build_with("client", "platform", canary)
        stdout = StringIO()
        stderr = StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            exit_code = validator.main(
                ["--fixture-only", "--fixture", str(self.fixture_path.relative_to(TEST_ROOT))]
            )
        combined = stdout.getvalue() + stderr.getvalue()
        self.assertEqual(exit_code, 2)
        self.assertNotIn(canary, combined)
        report = json.loads(stdout.getvalue())
        self.assertEqual(report["blockers"], ["PRIVACY_POLICY_VIOLATION"])
        self.assertFalse(report["real_capture_authorized"])

    def test_no_application_imports(self) -> None:
        tree = ast.parse(VALIDATOR_PATH.read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".", 1)[0].lower() for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".", 1)[0].lower())
        self.assertTrue(imported.isdisjoint(FORBIDDEN_MODULE_ROOTS), imported)

    def test_production_imports_are_exact_standard_library_allowlist(self) -> None:
        tree = ast.parse(VALIDATOR_PATH.read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".", 1)[0])
        self.assertEqual(
            imported,
            {
                "__future__",
                "argparse",
                "copy",
                "datetime",
                "fcntl",
                "hashlib",
                "hmac",
                "ipaddress",
                "json",
                "os",
                "pathlib",
                "re",
                "stat",
                "sys",
                "typing",
            },
        )

    def test_no_network_calls(self) -> None:
        tree = ast.parse(VALIDATOR_PATH.read_text(encoding="utf-8"))
        imported = set()
        called = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".", 1)[0].lower() for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".", 1)[0].lower())
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                called.add(node.func.id)
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                called.add(node.func.attr)
        self.assertTrue(
            imported.isdisjoint({"aiohttp", "chromadb", "http", "requests", "socket", "urllib"}),
            imported,
        )
        self.assertTrue(
            called.isdisjoint({"connect", "create_connection", "urlopen", "request"}),
            called,
        )

    def test_no_package_discovery(self) -> None:
        source = VALIDATOR_PATH.read_text(encoding="utf-8")
        for prohibited in (
            "importlib.metadata",
            "pkg_resources",
            "packages_distributions",
            "find_spec(",
            "subprocess",
            "pip",
        ):
            self.assertNotIn(prohibited, source)

    def test_no_filesystem_mutation_or_temporary_directory_probe(self) -> None:
        tree = ast.parse(VALIDATOR_PATH.read_text(encoding="utf-8"))
        prohibited_calls = {
            "TemporaryDirectory",
            "NamedTemporaryFile",
            "chmod",
            "chown",
            "link",
            "makedirs",
            "mkdir",
            "mkdtemp",
            "mkstemp",
            "remove",
            "rename",
            "rmdir",
            "symlink",
            "unlink",
            "write",
            "write_bytes",
            "write_text",
        }
        called = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                called.add(node.func.id)
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                called.add(node.func.attr)
        self.assertTrue(called.isdisjoint(prohibited_calls), called & prohibited_calls)
        source = VALIDATOR_PATH.read_text(encoding="utf-8")
        for prohibited in (
            "tempfile",
            "os.O_APPEND",
            "os.O_CREAT",
            "os.O_RDWR",
            "os.O_TRUNC",
            "os.O_WRONLY",
        ):
            self.assertNotIn(prohibited, source)

    def test_launcher_attested_temp_boundary_is_required(self) -> None:
        self.builder.build()
        with mock.patch.dict(
            os.environ,
            {"MARKETMATCH_CHROMA_PROVENANCE_TEMP_BOUNDARY": str(TEST_ROOT)},
        ):
            self.assert_error("READ_ERROR")

    def test_no_environment_secret_reads(self) -> None:
        tree = ast.parse(VALIDATOR_PATH.read_text(encoding="utf-8"))
        literals = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if (
                isinstance(node.func.value, ast.Attribute)
                and isinstance(node.func.value.value, ast.Name)
                and node.func.value.value.id == "os"
                and node.func.value.attr == "environ"
                and node.func.attr == "get"
                and node.args
                and isinstance(node.args[0], ast.Constant)
            ):
                literals.add(node.args[0].value)
        self.assertEqual(literals, validator.GATE_ENVIRONMENT)
        self.assertNotIn("os.getenv", VALIDATOR_PATH.read_text(encoding="utf-8"))

    def test_source_before_after_equality(self) -> None:
        self.builder.build()
        before = _filesystem_snapshot(self.fixture_path)
        self.validate()
        after = _filesystem_snapshot(self.fixture_path)
        self.assertEqual(before, after)

    def test_manifest_digest_tampering(self) -> None:
        self.builder.build()
        with _writable_fixture(self.fixture_path):
            path = self.fixture_path / "provenance-manifest.json"
            value = json.loads(path.read_text(encoding="utf-8"))
            value["canonical_sha256"] = ZERO_SHA256
            path.write_bytes(_json_bytes(value))
        self.assert_error("READ_ERROR")

    def test_contract_digest_tampering(self) -> None:
        self.builder.build()
        with mock.patch.object(validator, "CONTRACT_SHA256", "f" * 64):
            self.assert_error("READ_ERROR")

    def test_launcher_digest_tampering(self) -> None:
        self.builder.build()
        with mock.patch.object(validator, "TEST_LAUNCHER_SHA256", "e" * 64):
            self.assert_error("READ_ERROR")

    def test_invalid_fixture_provenance(self) -> None:
        self.builder.build(
            mutate_manifest=lambda manifest: manifest.__setitem__(
                "fixture_provenance", "REAL_CAPTURE"
            )
        )
        self.assert_error("INVALID_ATTESTATION")

    def test_failed_validation_never_reports_authorization(self) -> None:
        self.build_with("server", "image_tag", "latest")
        result = self.validate()
        self.assertFalse(result["valid"])
        self.assertFalse(result["real_capture_authorized"])

    def test_report_invariants_are_permanent_on_success_and_error(self) -> None:
        self.builder.build()
        reports = [self.validate(), validator._error_report("READ_ERROR")]
        for report in reports:
            for key, value in REPORT_INVARIANTS.items():
                self.assertEqual(report[key], value)

    def test_blocker_output_is_deterministic_and_sorted(self) -> None:
        def mutate(groups):
            groups["client"]["exact_version"] = "latest"
            groups["server"]["image_tag"] = "latest"
            groups["server"]["exact_build"] = "unknown"
            groups["connection_identity"]["tenant"] = "default"
            groups["connection_identity"]["deployment_mode"] = "remote_server"
            groups["persistence"]["before_after_equal"] = False
            groups["decision"]["client_server_compatibility"] = "MISMATCH"

        self.builder.build(mutate_groups=mutate)
        first = self.validate()["blockers"]
        second = self.validate()["blockers"]
        self.assertEqual(first, second)
        self.assertEqual(first, sorted(set(first)))
        self.assertTrue(set(first).issubset(ALL_STATUS_CODES))

    def test_contract_digest_is_canonical_and_pinned(self) -> None:
        self.assertEqual(CONTRACT_SHA256, _canonical_digest(CONTRACT))
        self.assertEqual(validator.CONTRACT_SHA256, CONTRACT_SHA256)

    def test_launcher_inode_and_digest_are_pinned_by_contract(self) -> None:
        launcher_sha256 = hashlib.sha256(LAUNCHER_PATH.read_bytes()).hexdigest()
        self.assertEqual(CONTRACT["fixture_gate"]["required_launcher_sha256"], launcher_sha256)
        self.assertEqual(validator.TEST_LAUNCHER_SHA256, launcher_sha256)

    def test_recognized_deployment_modes_are_exact(self) -> None:
        self.assertEqual(
            CONTRACT["deployment_modes"],
            {
                "recognized": [
                    "docker_compose_local",
                    "native_local_server",
                    "packaged_external_server",
                    "preexisting_local_server",
                    "remote_server",
                    "synthetic_fixture",
                ],
                "valid_fixture_input": ["synthetic_fixture"],
            },
        )

    def test_all_nine_known_collection_identities_are_accepted_as_synthetic(self) -> None:
        def mutate(groups):
            items = [
                _collection(name)
                for name in sorted(
                    CONTRACT["collection_contract"]["physical_collections"]
                )
            ]
            inventory = _value_digest(items)
            groups["collections"]["items"] = items
            groups["collections"]["inventory_before_sha256"] = inventory
            groups["collections"]["inventory_after_sha256"] = inventory

        self.builder.build(mutate_groups=mutate)
        result = self.validate()
        self.assertTrue(result["valid"])
        self.assertEqual(result["collection_count"], 9)

    def test_adapter_commit_and_contract_digest_must_match_bound_source(self) -> None:
        cases = (
            ("adapter-commit", "adapter_commit", "1234567890abcdef1234567890abcdef12345678"),
            ("contract-digest", "adapter_contract_sha256", "ab" * 32),
        )
        for label, key, value in cases:
            with self.subTest(label=label):
                path = TEST_ROOT / "fixtures" / ("fixture-" + uuid.uuid4().hex)
                FixtureBuilder(path).build(
                    mutate_groups=lambda groups, key=key, value=value: groups[
                        "adapter"
                    ].__setitem__(key, value)
                )
                try:
                    result = self.validate(path)
                    self.assertFalse(result["valid"])
                    self.assertFalse(result["real_capture_authorized"])
                finally:
                    path.chmod(0o700)
                    for child in path.iterdir():
                        child.chmod(0o600)
                    shutil.rmtree(path)

    def test_contract_status_codes_are_exact(self) -> None:
        self.assertEqual(set(CONTRACT["status_codes"]), ALL_STATUS_CODES)

    def test_fixture_requires_fixture_only_flag(self) -> None:
        self.builder.build()
        with self.assertRaises(validator.ContractError) as raised:
            validator.validate_fixture_contract(
                str(self.fixture_path.relative_to(TEST_ROOT)), fixture_only=False
            )
        self.assertEqual(raised.exception.code, "READ_ERROR")

    def test_raw_tenant_and_database_values_are_not_reported(self) -> None:
        canary = "raw-tenant-canary"
        self.build_with("connection_identity", "tenant", canary)
        stdout = StringIO()
        stderr = StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            exit_code = validator.main(
                ["--fixture-only", "--fixture", str(self.fixture_path.relative_to(TEST_ROOT))]
            )
        self.assertEqual(exit_code, 2)
        self.assertNotIn(canary, stdout.getvalue() + stderr.getvalue())
        self.assertEqual(
            json.loads(stdout.getvalue())["blockers"], ["PRIVACY_POLICY_VIOLATION"]
        )

    def test_environment_secret_canary_is_refused_without_value_read_or_echo(self) -> None:
        self.builder.build()
        canary = "phase3g-environment-secret-canary"
        stdout = StringIO()
        stderr = StringIO()
        with mock.patch.dict(os.environ, {"DATABASE_URL": canary}):
            with redirect_stdout(stdout), redirect_stderr(stderr):
                exit_code = validator.main(
                    [
                        "--fixture-only",
                        "--fixture",
                        str(self.fixture_path.relative_to(TEST_ROOT)),
                    ]
                )
        self.assertEqual(exit_code, 2)
        self.assertNotIn(canary, stdout.getvalue() + stderr.getvalue())
        self.assertEqual(json.loads(stdout.getvalue())["blockers"], ["READ_ERROR"])

    def test_runtime_path_variables_are_refused_by_launcher_and_validator(self) -> None:
        launcher_source = LAUNCHER_PATH.read_text(encoding="utf-8")
        blocked_body = launcher_source.split("blocked_variables=(\n", 1)[1].split(
            "\n)", 1
        )[0]
        launcher_blocked = {
            line.strip() for line in blocked_body.splitlines() if line.strip()
        }
        self.assertEqual(
            launcher_blocked,
            set(validator.BLOCKED_ENVIRONMENT) | set(validator.GATE_ENVIRONMENT),
        )
        required = {
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
        }
        self.assertTrue(required.issubset(set(validator.BLOCKED_ENVIRONMENT)))
        for variable in required:
            self.assertIn("  " + variable + "\n", launcher_source)
        self.assertIn('temp_base="/tmp"', launcher_source)


if __name__ == "__main__":
    unittest.main()
