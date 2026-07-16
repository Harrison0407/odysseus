from __future__ import annotations

import copy
from contextlib import closing
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import sqlite3
import stat
import subprocess
import sys
import unittest
import uuid


REPO_ROOT = Path(__file__).resolve().parents[2]
BUILDER_PATH = REPO_ROOT / "scripts" / "build_marketmatch_sanitized_package.py"
SCHEMA_PATH = REPO_ROOT / "scripts" / "schema_contracts" / "combined_9844a2f_6197984.json"
PROHIBITED_MODULE_ROOTS = {
    "app",
    "core",
    "routes",
    "src",
    "services",
    "sqlalchemy",
    "chromadb",
}
PINNED_INTERNAL = ["internal-tool", "api", "demo", "system"]
NOW = "2000-01-01 00:00:00"


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_digest(payload: dict[str, object]) -> str:
    clean = copy.deepcopy(payload)
    clean.pop("canonical_sha256", None)
    encoded = (
        json.dumps(clean, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def load_builder():
    spec = importlib.util.spec_from_file_location("fixture_only_package_builder", BUILDER_PATH)
    if spec is None or spec.loader is None:
        raise AssertionError("could not load builder module")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


builder = load_builder()


class FixtureCase:
    def __init__(self, test: unittest.TestCase, *, include_fts: bool = True):
        self.test = test
        self.launcher_root = Path(os.environ["MARKETMATCH_OFFLINE_PACKAGE_TEST_ROOT"])
        self.sentinel = os.environ["MARKETMATCH_OFFLINE_PACKAGE_SENTINEL"]
        self.case_id = f"case-{uuid.uuid4().hex}"
        self.source_rel = Path("sources") / self.case_id
        self.source = self.launcher_root / self.source_rel
        self.destination_rel = Path("destinations") / f"{self.case_id}-package"
        self.destination = self.launcher_root / self.destination_rel
        self.database = self.source / "database" / "source.sqlite"
        self.schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        self.canaries = {
            "password": f"PASSWORD_HASH_CANARY_{uuid.uuid4().hex}",
            "session": f"SESSION_TOKEN_CANARY_{uuid.uuid4().hex}",
            "api": f"API_TOKEN_CANARY_{uuid.uuid4().hex}",
            "totp": f"TOTP_SECRET_CANARY_{uuid.uuid4().hex}",
            "prompt": f"PROMPT_CANARY_{uuid.uuid4().hex}",
            "message": f"MESSAGE_CANARY_{uuid.uuid4().hex}",
            "response": f"RESPONSE_CANARY_{uuid.uuid4().hex}",
            "document": f"DOCUMENT_BODY_CANARY_{uuid.uuid4().hex}",
            "email": f"EMAIL_BODY_CANARY_{uuid.uuid4().hex}",
            "transcript": f"TRANSCRIPT_CANARY_{uuid.uuid4().hex}",
            "summary": f"SUMMARY_CANARY_{uuid.uuid4().hex}",
            "media": f"MEDIA_CANARY_{uuid.uuid4().hex}",
            "filename": f"ORIGINAL_FILENAME_CANARY_{uuid.uuid4().hex}",
            "client_ip": f"CLIENT_IP_CANARY_{uuid.uuid4().hex}",
            "nested": f"NESTED_JSON_CANARY_{uuid.uuid4().hex}",
            "fts": f"FTS_CANARY_{uuid.uuid4().hex}",
            "deleted": f"DELETED_FREELIST_CANARY_{uuid.uuid4().hex}",
        }
        self.source.mkdir(parents=True)
        (self.source / "database").mkdir()
        (self.source / "artifacts").mkdir()
        self._create_database(include_fts=include_fts)
        self._create_artifacts()
        self._write_inputs()

    def cleanup(self) -> None:
        shutil.rmtree(self.source, ignore_errors=True)
        if self.destination.is_symlink() or self.destination.is_file():
            self.destination.unlink(missing_ok=True)
        else:
            shutil.rmtree(self.destination, ignore_errors=True)
        for candidate in self.destination.parent.glob(f".{self.destination.name}.building-*"):
            shutil.rmtree(candidate, ignore_errors=True)

    def _create_database(self, *, include_fts: bool) -> None:
        connection = sqlite3.connect(self.database)
        try:
            for table in self.schema["tables"].values():
                connection.execute(table["raw_sql_variants"][0])
            for table in self.schema["tables"].values():
                for index in table["indexes"]:
                    raw_sql = index["raw_sql_variants"][0]
                    if raw_sql is not None:
                        connection.execute(raw_sql)
            if include_fts:
                fts = self.schema["fts_contract"]
                connection.execute(fts["objects"]["chat_messages_fts"]["raw_sql_variants"][0])
                for name in ("chat_messages_fts_ai", "chat_messages_fts_ad", "chat_messages_fts_au"):
                    connection.execute(fts["objects"][name]["raw_sql_variants"][0])

            connection.execute(
                "INSERT INTO sessions "
                "(id,name,endpoint_url,model,owner,headers,mode,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    "session-source-id",
                    self.canaries["session"],
                    "https://fixture.invalid",
                    "fixture-model",
                    "AliceFixture",
                    json.dumps({"fixture_label": self.canaries["nested"]}),
                    builder._synthetic_fixture_proof(self.sentinel, self.case_id),
                    NOW,
                    NOW,
                ),
            )
            connection.execute(
                "INSERT INTO documents "
                "(id,session_id,title,current_content,owner,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (
                    "document-source-id",
                    "session-source-id",
                    self.canaries["email"],
                    self.canaries["document"],
                    "AliceFixture",
                    NOW,
                    NOW,
                ),
            )
            connection.execute(
                "INSERT INTO documents "
                "(id,session_id,title,current_content,owner,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (
                    "summary-document-source-id",
                    "session-source-id",
                    self.canaries["summary"],
                    self.canaries["summary"],
                    "AliceFixture",
                    NOW,
                    NOW,
                ),
            )
            call_path = str(
                (
                    self.source
                    / "artifacts"
                    / "call-audio"
                    / self.canaries["filename"]
                ).resolve()
            )
            video_path = str(
                (self.source / "artifacts" / "video-media" / "video-source.bin").resolve()
            )
            connection.execute(
                "INSERT INTO marketmatch_calls "
                "(id,owner,original_filename,stored_audio_path,transcript_path,summary_path,"
                "status,upload_id,document_id,summary_document_id,workflow_id,error_message,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    "call-source-id",
                    "AliceFixture",
                    self.canaries["filename"],
                    call_path,
                    self.canaries["transcript"],
                    self.canaries["summary"],
                    "complete",
                    "upload-audio-source-id",
                    "document-source-id",
                    "summary-document-source-id",
                    "workflow-source-id",
                    self.canaries["response"],
                    NOW,
                    NOW,
                ),
            )
            connection.execute(
                "INSERT INTO marketmatch_videos "
                "(id,owner,original_filename,stored_video_path,status,upload_id,document_id,"
                "summary_document_id,error_message,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    "video-source-id",
                    "AliceFixture",
                    "video-source.bin",
                    video_path,
                    "complete",
                    "upload-video-source-id",
                    "document-source-id",
                    "summary-document-source-id",
                    self.canaries["response"],
                    NOW,
                    NOW,
                ),
            )
            connection.execute(
                "INSERT INTO api_tokens "
                "(id,owner,name,token_hash,token_prefix,scopes,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,?,?)",
                ("api-id", "AliceFixture", "fixture", self.canaries["api"], "secret", "all", NOW, NOW),
            )
            connection.execute(
                "INSERT INTO provider_auth_sessions "
                "(id,provider,owner,base_url,access_token,refresh_token,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (
                    "provider-id",
                    "fixture",
                    "AliceFixture",
                    "https://fixture.invalid",
                    self.canaries["password"],
                    self.canaries["totp"],
                    NOW,
                    NOW,
                ),
            )
            connection.execute(
                "INSERT INTO comparisons "
                "(id,owner,prompt,model_a,model_b,endpoint_a,endpoint_b,response_a,response_b,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    "comparison-id",
                    "AliceFixture",
                    self.canaries["prompt"],
                    "a",
                    "b",
                    "a",
                    "b",
                    self.canaries["response"],
                    self.canaries["response"],
                    NOW,
                    NOW,
                ),
            )
            connection.execute(
                "INSERT INTO notes (id,owner,title,content,created_at,updated_at) VALUES (?,?,?,?,?,?)",
                ("note-id", "AliceFixture", "fixture", self.canaries["summary"], NOW, NOW),
            )
            connection.execute(
                "INSERT INTO chat_messages (id,session_id,role,content,metadata,timestamp) VALUES (?,?,?,?,?,?)",
                (
                    "message-id",
                    "session-source-id",
                    "user",
                    self.canaries["fts"] if include_fts else self.canaries["message"],
                    json.dumps({"client_ip": self.canaries["client_ip"]}),
                    NOW,
                ),
            )
            connection.execute(
                "CREATE TABLE fixture_deleted_canary(value TEXT)"
            )
            connection.execute(
                "INSERT INTO fixture_deleted_canary(value) VALUES (?)",
                (self.canaries["deleted"],),
            )
            connection.commit()
            connection.execute("DROP TABLE fixture_deleted_canary")
            connection.commit()
        finally:
            connection.close()

    def _create_artifacts(self) -> None:
        (self.source / "artifacts" / "call-audio").mkdir()
        (self.source / "artifacts" / "video-media").mkdir()
        (self.source / "artifacts" / "call-audio" / self.canaries["filename"]).write_bytes(
            self.canaries["media"].encode("utf-8")
        )
        (self.source / "artifacts" / "video-media" / "video-source.bin").write_bytes(
            (self.canaries["media"] + "-video").encode("utf-8")
        )

    def _write_inputs(self) -> None:
        (self.source / ".fixture-sentinel.json").write_text(
            json.dumps(
                {
                    "classification": "SYNTHETIC_DISPOSABLE_FIXTURE",
                    "fixture_id": self.case_id,
                    "launcher_sentinel": self.sentinel,
                },
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        owners = {
            "active": ["AliceFixture"],
            "retired": ["RetiredFixture"],
            "legacy_approved": ["LegacyFixture"],
            "internal": PINNED_INTERNAL,
        }
        (self.source / "owners.synthetic.json").write_text(
            json.dumps(owners, sort_keys=True) + "\n", encoding="utf-8"
        )
        uploads = [
            {
                "upload_id": "upload-audio-source-id",
                "owner": "AliceFixture",
                "source_relative_path": f"artifacts/call-audio/{self.canaries['filename']}",
                "category": "call-audio",
            },
            {
                "upload_id": "upload-video-source-id",
                "owner": "AliceFixture",
                "source_relative_path": "artifacts/video-media/video-source.bin",
                "category": "video-media",
            },
        ]
        (self.source / "uploads.synthetic.json").write_text(
            json.dumps(uploads, sort_keys=True) + "\n", encoding="utf-8"
        )
        provenance = {
            "classification": "SYNTHETIC_DISPOSABLE_FIXTURE",
            "fixture_id": self.case_id,
            "launcher_sentinel": self.sentinel,
            "database": {
                "relative_path": "database/source.sqlite",
                "sha256": sha256_path(self.database),
            },
        }
        (self.source / "provenance.json").write_text(
            json.dumps(provenance, sort_keys=True) + "\n", encoding="utf-8"
        )

    def refresh_database_hash(self) -> None:
        path = self.source / "provenance.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["database"]["sha256"] = sha256_path(self.database)
        path.write_text(json.dumps(data, sort_keys=True) + "\n", encoding="utf-8")

    def build(self, destination_rel: Path | None = None) -> dict[str, object]:
        return builder.build_fixture_package(
            source=str(self.source_rel),
            destination=str(destination_rel or self.destination_rel),
            fixture_only=True,
        )


class OfflinePackageBuilderTests(unittest.TestCase):
    maxDiff = None

    def setUp(self) -> None:
        self.fixtures: list[FixtureCase] = []

    def tearDown(self) -> None:
        for fixture in self.fixtures:
            fixture.cleanup()

    def fixture(self, **kwargs: object) -> FixtureCase:
        fixture = FixtureCase(self, **kwargs)
        self.fixtures.append(fixture)
        return fixture

    def assert_builder_error(self, fixture: FixtureCase, code: str) -> builder.BuilderError:
        with self.assertRaises(builder.BuilderError) as caught:
            fixture.build()
        self.assertEqual(caught.exception.code, code)
        if fixture.destination.exists():
            self.assertTrue((fixture.destination / "INVALID-FIXTURE-PACKAGE").is_file())
            self.assertFalse((fixture.destination / "package-manifest.json").exists())
        self.assertFalse(any(fixture.destination.parent.glob(f".{fixture.destination.name}.building-*")))
        return caught.exception

    def test_successful_fixture_build_is_fresh_sealed_and_explicitly_blocked(self) -> None:
        fixture = self.fixture()
        source_hash = sha256_path(fixture.database)
        report = fixture.build()
        self.assertEqual(report["status"], "FIXTURE_PACKAGE_CREATED")
        self.assertEqual(source_hash, sha256_path(fixture.database))
        package = json.loads((fixture.destination / "package-manifest.json").read_text())
        self.assertEqual(package["rag_status"], "RAG_EXPORT_NOT_AVAILABLE")
        self.assertEqual(package["authorization"], "NOT_AUTHORIZED_FOR_REAL_INSPECTION")
        self.assertFalse(package["complete"])
        provenance = json.loads(
            (fixture.destination / "provenance" / "provenance.json").read_text()
        )
        self.assertTrue(provenance["source_mutation_relevant_fields_unchanged"])
        self.assertFalse(provenance["access_time_compared"])
        self.assertNotIn("source_non_mutation_verified", provenance)
        database = fixture.destination / "database" / "main.sqlite"
        self.assertTrue(database.is_file())
        self.assertEqual(database.stat().st_nlink, 1)
        self.assertFalse(Path(str(database) + "-wal").exists())
        self.assertFalse(Path(str(database) + "-shm").exists())
        self.assertFalse(Path(str(database) + "-journal").exists())
        with closing(sqlite3.connect(database)) as connection:
            self.assertEqual(connection.execute("PRAGMA freelist_count").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT count(*) FROM chat_messages").fetchone()[0], 0)
            fts_count = connection.execute("SELECT count(*) FROM chat_messages_fts").fetchone()[0]
            self.assertEqual(fts_count, 0)
            schema = json.loads(SCHEMA_PATH.read_text())
            expected_objects = set(schema["allowed_object_names"]["always"]) | set(
                schema["fts_contract"]["object_names"]
            )
            actual_objects = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_schema WHERE name <> 'sqlite_schema'"
                )
            }
            self.assertEqual(actual_objects, expected_objects)
            for table in schema["tables"]:
                expected_count = 2 if table == "documents" else 1 if table in {
                    "sessions",
                    "marketmatch_calls",
                    "marketmatch_videos",
                } else 0
                self.assertEqual(
                    connection.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0],
                    expected_count,
                )
        self.assertFalse(any("rag" in path.name.casefold() for path in fixture.destination.rglob("*")))

    def test_source_before_after_snapshot_is_identical(self) -> None:
        fixture = self.fixture()
        before = builder.snapshot_fixture_source(fixture.source)
        fixture.build()
        after = builder.snapshot_fixture_source(fixture.source)
        self.assertEqual(before, after)

    def test_unknown_table_column_and_schema_objects_are_rejected(self) -> None:
        mutations = (
            ("unknown_table", "CREATE TABLE unexpected_table(id TEXT)", "UNKNOWN_SOURCE_TABLE"),
            ("unknown_column", "ALTER TABLE sessions ADD COLUMN unexpected TEXT", "UNKNOWN_SOURCE_COLUMN"),
            ("unknown_index", "CREATE INDEX collision_index ON sessions(name)", "UNKNOWN_SCHEMA_OBJECT"),
            ("unknown_view", "CREATE VIEW collision_view AS SELECT id FROM sessions", "UNKNOWN_SCHEMA_OBJECT"),
            (
                "unknown_trigger",
                "CREATE TRIGGER collision_trigger AFTER UPDATE ON sessions BEGIN SELECT 1; END",
                "UNKNOWN_SCHEMA_OBJECT",
            ),
        )
        for label, sql, code in mutations:
            with self.subTest(label=label):
                fixture = self.fixture()
                with closing(sqlite3.connect(fixture.database)) as connection:
                    connection.execute(sql)
                    connection.commit()
                fixture.refresh_database_hash()
                self.assert_builder_error(fixture, code)

    def test_partial_or_unknown_fts_layout_is_rejected(self) -> None:
        fixture = self.fixture()
        with closing(sqlite3.connect(fixture.database)) as connection:
            connection.execute("DROP TRIGGER chat_messages_fts_au")
            connection.commit()
        fixture.refresh_database_hash()
        self.assert_builder_error(fixture, "INCOMPATIBLE_FTS_GROUP")

    def test_unknown_transform_action_and_nonexhaustive_contract_are_rejected(self) -> None:
        transform = json.loads(builder.TRANSFORM_CONTRACT_PATH.read_text(encoding="utf-8"))
        bad = copy.deepcopy(transform)
        bad["tables"]["sessions"]["columns"]["name"]["action"] = "INVENTED_ACTION"
        with self.assertRaises(builder.BuilderError) as caught:
            builder.validate_transform_contract(bad, builder.load_schema_contract())
        self.assertEqual(caught.exception.code, "UNKNOWN_TRANSFORM_ACTION")
        incomplete = copy.deepcopy(transform)
        del incomplete["tables"]["sessions"]["columns"]["name"]
        with self.assertRaises(builder.BuilderError) as caught:
            builder.validate_transform_contract(incomplete, builder.load_schema_contract())
        self.assertEqual(caught.exception.code, "NONEXHAUSTIVE_TRANSFORM_CONTRACT")

    def test_nested_json_unknown_key_is_rejected(self) -> None:
        fixture = self.fixture()
        with closing(sqlite3.connect(fixture.database)) as connection:
            connection.execute(
                "UPDATE sessions SET headers=? WHERE id=?",
                (json.dumps({"fixture_label": "safe", "unknown_secret": fixture.canaries["nested"]}), "session-source-id"),
            )
            connection.commit()
        fixture.refresh_database_hash()
        self.assert_builder_error(fixture, "UNSUPPORTED_NESTED_JSON")

    def test_aliases_are_consistent_within_package_and_different_across_builds(self) -> None:
        fixture = self.fixture()
        second_rel = Path("destinations") / f"{fixture.case_id}-second"
        first = fixture.build()
        second = fixture.build(second_rel)
        second_path = fixture.launcher_root / second_rel
        try:
            with closing(
                sqlite3.connect(fixture.destination / "database" / "main.sqlite")
            ) as connection:
                owners = {
                    row[0]
                    for table in ("sessions", "documents", "marketmatch_calls", "marketmatch_videos")
                    for row in connection.execute(f"SELECT owner FROM {table}")
                }
                first_call = connection.execute(
                    "SELECT owner,upload_id,document_id FROM marketmatch_calls"
                ).fetchone()
            owner_manifest = json.loads(
                (fixture.destination / "manifests" / "owners.sanitized.json").read_text()
            )
            upload_manifest = json.loads(
                (fixture.destination / "manifests" / "uploads.sanitized.json").read_text()
            )
            self.assertEqual(len(owners), 1)
            alias = next(iter(owners))
            self.assertIn(alias, owner_manifest["active"])
            self.assertEqual({entry["owner"] for entry in upload_manifest}, {alias})
            self.assertEqual(first_call[1], upload_manifest[0]["upload_id"])
            with closing(sqlite3.connect(second_path / "database" / "main.sqlite")) as connection:
                second_owner = connection.execute("SELECT owner FROM sessions").fetchone()[0]
            self.assertNotEqual(alias, second_owner)
            self.assertNotEqual(first["package_id"], second["package_id"])
        finally:
            shutil.rmtree(second_path, ignore_errors=True)

    def test_internal_owner_policy_collision_and_unknown_classification(self) -> None:
        for category in ("active", "retired", "legacy_approved"):
            with self.subTest(category=category):
                fixture = self.fixture()
                path = fixture.source / "owners.synthetic.json"
                owners = json.loads(path.read_text())
                owners[category].append("system")
                path.write_text(json.dumps(owners, sort_keys=True) + "\n")
                self.assert_builder_error(fixture, "RESERVED_INTERNAL_OWNER")

        fixture = self.fixture()
        path = fixture.source / "owners.synthetic.json"
        owners = json.loads(path.read_text())
        owners["retired"].append("alicefixture")
        path.write_text(json.dumps(owners, sort_keys=True) + "\n")
        self.assert_builder_error(fixture, "OWNER_REGISTRY_COLLISION")

        fixture = self.fixture()
        with closing(sqlite3.connect(fixture.database)) as connection:
            for table in ("sessions", "documents", "marketmatch_calls", "marketmatch_videos"):
                connection.execute(f"UPDATE {table} SET owner='UnknownFixture'")
            connection.commit()
        uploads_path = fixture.source / "uploads.synthetic.json"
        uploads = json.loads(uploads_path.read_text())
        for upload in uploads:
            upload["owner"] = "UnknownFixture"
        uploads_path.write_text(json.dumps(uploads, sort_keys=True) + "\n")
        fixture.refresh_database_hash()
        fixture.build()
        attestation = json.loads(
            (fixture.destination / "manifests" / "transform-attestation.json").read_text()
        )
        self.assertGreaterEqual(attestation["owner_classification_counts"]["unknown"], 1)
        self.assertNotIn("UnknownFixture", json.dumps(attestation))

        fixture = self.fixture()
        with closing(sqlite3.connect(fixture.database)) as connection:
            for table in ("sessions", "documents", "marketmatch_calls", "marketmatch_videos"):
                connection.execute(f"UPDATE {table} SET owner='system'")
            connection.commit()
        uploads_path = fixture.source / "uploads.synthetic.json"
        uploads = json.loads(uploads_path.read_text())
        for upload in uploads:
            upload["owner"] = "system"
        uploads_path.write_text(json.dumps(uploads, sort_keys=True) + "\n")
        fixture.refresh_database_hash()
        fixture.build()
        with closing(
            sqlite3.connect(fixture.destination / "database" / "main.sqlite")
        ) as connection:
            self.assertEqual(connection.execute("SELECT owner FROM sessions").fetchone()[0], "system")

    def test_artifacts_are_zero_byte_opaque_placeholders_and_content_is_never_copied(self) -> None:
        fixture = self.fixture()
        for source_artifact in (fixture.source / "artifacts").rglob("*"):
            if source_artifact.is_file():
                source_artifact.chmod(0)
        fixture.build()
        manifest = json.loads(
            (fixture.destination / "manifests" / "artifacts.sanitized.json").read_text()
        )
        self.assertEqual(len(manifest), 2)
        for record in manifest:
            self.assertEqual(record["destination_size"], 0)
            placeholder = fixture.destination / record["approved_relative_path_token"]
            self.assertEqual(placeholder.read_bytes(), b"")
            self.assertEqual(placeholder.stat().st_nlink, 1)
        output_text = "\n".join(
            str(path.relative_to(fixture.destination))
            for path in fixture.destination.rglob("*")
        ) + "\n" + json.dumps(manifest)
        self.assertNotIn(fixture.canaries["filename"], output_text)
        self.assertNotIn(str(fixture.source.resolve()), output_text)

    def test_special_artifact_files_are_rejected(self) -> None:
        cases = ("symlink", "hardlink", "fifo")
        for kind in cases:
            with self.subTest(kind=kind):
                fixture = self.fixture()
                target = fixture.source / "artifacts" / "special"
                upload_path = fixture.source / "uploads.synthetic.json"
                uploads = json.loads(upload_path.read_text())
                uploads[0]["source_relative_path"] = "artifacts/special"
                upload_path.write_text(json.dumps(uploads, sort_keys=True) + "\n")
                cleanup_socket = None
                if kind == "symlink":
                    target.symlink_to(
                        fixture.source / "artifacts" / "video-media" / "video-source.bin"
                    )
                    expected = "SYMLINK_REJECTED"
                elif kind == "hardlink":
                    os.link(
                        fixture.source / "artifacts" / "video-media" / "video-source.bin",
                        target,
                    )
                    expected = "HARD_LINK_REJECTED"
                elif kind == "fifo":
                    os.mkfifo(target)
                    expected = "SPECIAL_FILE_REJECTED"
                try:
                    self.assert_builder_error(fixture, expected)
                finally:
                    if cleanup_socket is not None:
                        cleanup_socket.close()
        with self.assertRaises(builder.BuilderError) as caught:
            builder.reject_special_mode(stat.S_IFCHR)
        self.assertEqual(caught.exception.code, "SPECIAL_FILE_REJECTED")
        with self.assertRaises(builder.BuilderError) as caught:
            builder.reject_special_mode(stat.S_IFSOCK)
        self.assertEqual(caught.exception.code, "SPECIAL_FILE_REJECTED")

    def test_source_destination_overlap_existing_destination_and_sidecars_are_rejected(self) -> None:
        fixture = self.fixture()
        with self.assertRaises(builder.BuilderError) as caught:
            builder.build_fixture_package(
                source=str(fixture.source_rel),
                destination=str(fixture.source_rel / "package"),
                fixture_only=True,
            )
        self.assertEqual(caught.exception.code, "PATH_OUTSIDE_FIXTURE_ROOT")

        fixture = self.fixture()
        fixture.destination.mkdir()
        with self.assertRaises(builder.BuilderError) as caught:
            fixture.build()
        self.assertEqual(caught.exception.code, "DESTINATION_EXISTS")
        self.assertTrue(fixture.destination.is_dir())

        fixture = self.fixture()
        Path(str(fixture.database) + "-wal").write_bytes(b"fixture-sidecar")
        self.assert_builder_error(fixture, "SOURCE_SIDECAR_PRESENT")

    def test_destination_parent_and_owned_directory_swaps_fail_without_redirected_writes(self) -> None:
        fixture = self.fixture()
        (
            _root,
            _source,
            destination,
            _sentinel,
            parent_identity,
        ) = builder._approved_paths(str(fixture.source_rel), str(fixture.destination_rel))
        original_parent = destination.parent
        moved_parent = fixture.launcher_root / "destinations-original"
        original_parent.rename(moved_parent)
        original_parent.mkdir()
        try:
            with self.assertRaises(builder.BuilderError) as caught:
                builder.DestinationTree.create(
                    destination, expected_parent_identity=parent_identity
                )
            self.assertEqual(caught.exception.code, "DESTINATION_PARENT_REPLACED")
            self.assertEqual(list(original_parent.iterdir()), [])
        finally:
            original_parent.rmdir()
            moved_parent.rename(original_parent)

        fixture = self.fixture()
        moved_destination = fixture.destination.parent / f"{fixture.destination.name}-moved"
        original_build = builder._build_staging

        def swap_after_build(*args: object, **kwargs: object):
            result = original_build(*args, **kwargs)
            fixture.destination.rename(moved_destination)
            fixture.destination.mkdir()
            (fixture.destination / "replacement-sentinel").write_text("untouched\n")
            return result

        builder._build_staging = swap_after_build
        try:
            with self.assertRaises(builder.BuilderError) as caught:
                fixture.build()
            self.assertEqual(caught.exception.code, "DESTINATION_REPLACED")
            self.assertEqual(
                (fixture.destination / "replacement-sentinel").read_text(), "untouched\n"
            )
            self.assertEqual(
                {path.name for path in fixture.destination.iterdir()},
                {"replacement-sentinel"},
            )
            self.assertTrue((moved_destination / "INVALID-FIXTURE-PACKAGE").is_file())
            self.assertFalse((moved_destination / "package-manifest.json").exists())
        finally:
            builder._build_staging = original_build
            shutil.rmtree(moved_destination, ignore_errors=True)

    def test_hash_mismatch_and_missing_fixture_gate_are_rejected(self) -> None:
        fixture = self.fixture()
        provenance_path = fixture.source / "provenance.json"
        provenance = json.loads(provenance_path.read_text())
        provenance["database"]["sha256"] = "0" * 64
        provenance_path.write_text(json.dumps(provenance, sort_keys=True) + "\n")
        self.assert_builder_error(fixture, "SOURCE_HASH_MISMATCH")

        fixture = self.fixture()
        with self.assertRaises(builder.BuilderError) as caught:
            builder.build_fixture_package(
                source=str(fixture.source_rel),
                destination=str(fixture.destination_rel),
                fixture_only=False,
            )
        self.assertEqual(caught.exception.code, "FIXTURE_ONLY_FLAG_REQUIRED")

    def test_launcher_capability_synthetic_proof_and_exact_profile_are_required(self) -> None:
        fixture = self.fixture()
        original_fd = os.environ["MARKETMATCH_OFFLINE_PACKAGE_SENTINEL_FD"]
        os.environ["MARKETMATCH_OFFLINE_PACKAGE_SENTINEL_FD"] = "999999"
        try:
            self.assert_builder_error(fixture, "FIXTURE_LAUNCHER_REQUIRED")
        finally:
            os.environ["MARKETMATCH_OFFLINE_PACKAGE_SENTINEL_FD"] = original_fd

        fixture = self.fixture()
        original_launcher_fd = os.environ["MARKETMATCH_OFFLINE_PACKAGE_LAUNCHER_FD"]
        os.environ["MARKETMATCH_OFFLINE_PACKAGE_LAUNCHER_FD"] = original_fd
        try:
            self.assert_builder_error(fixture, "FIXTURE_LAUNCHER_REQUIRED")
        finally:
            os.environ["MARKETMATCH_OFFLINE_PACKAGE_LAUNCHER_FD"] = original_launcher_fd

        fixture = self.fixture()
        with closing(sqlite3.connect(fixture.database)) as connection:
            connection.execute("UPDATE sessions SET mode='self-attested-only'")
            connection.commit()
        fixture.refresh_database_hash()
        self.assert_builder_error(fixture, "SYNTHETIC_FIXTURE_PROOF_INVALID")

        fixture = self.fixture()
        with closing(sqlite3.connect(fixture.database)) as connection:
            connection.execute("DELETE FROM api_tokens")
            connection.commit()
        fixture.refresh_database_hash()
        self.assert_builder_error(fixture, "SYNTHETIC_FIXTURE_PROFILE_MISMATCH")

    def test_marketmatch_graph_requires_complete_same_owner_links(self) -> None:
        mutations = (
            (
                "ownerless_workflow",
                "UPDATE marketmatch_calls SET owner=NULL WHERE id='call-source-id'",
                "EMPTY_WORKFLOW_OWNER",
            ),
            (
                "document_session_owner",
                "UPDATE documents SET owner='BobFixture' WHERE id='document-source-id'",
                "CROSS_OWNER_DOCUMENT_SESSION",
            ),
            (
                "workflow_document_owner",
                "UPDATE documents SET owner='BobFixture',session_id=NULL "
                "WHERE id='summary-document-source-id'",
                "CROSS_OWNER_DOCUMENT_REFERENCE",
            ),
            (
                "missing_document",
                "UPDATE marketmatch_calls SET summary_document_id='missing-document' "
                "WHERE id='call-source-id'",
                "MISSING_DOCUMENT_REFERENCE",
            ),
            (
                "duplicate_workflow",
                "UPDATE marketmatch_calls SET workflow_id='video-source-id' "
                "WHERE id='call-source-id'",
                "DUPLICATE_WORKFLOW_IDENTIFIER",
            ),
        )
        for label, sql, code in mutations:
            with self.subTest(label=label):
                fixture = self.fixture()
                with closing(sqlite3.connect(fixture.database)) as connection:
                    connection.execute(sql)
                    connection.commit()
                fixture.refresh_database_hash()
                self.assert_builder_error(fixture, code)

    def test_absolute_traversal_symlinked_root_and_shared_inode_are_rejected(self) -> None:
        fixture = self.fixture()
        for source, destination in (
            (str(fixture.source), str(fixture.destination_rel)),
            ("sources/../sources/fixture", str(fixture.destination_rel)),
            (str(fixture.source_rel), "destinations/../sources/fixture"),
        ):
            with self.subTest(source=source, destination=destination):
                with self.assertRaises(builder.BuilderError) as caught:
                    builder.build_fixture_package(
                        source=source,
                        destination=destination,
                        fixture_only=True,
                    )
                self.assertEqual(caught.exception.code, "PATH_OUTSIDE_FIXTURE_ROOT")

        root_link = fixture.launcher_root / "fixture-root-link"
        root_link.symlink_to(fixture.launcher_root, target_is_directory=True)
        original_root = os.environ["MARKETMATCH_OFFLINE_PACKAGE_TEST_ROOT"]
        os.environ["MARKETMATCH_OFFLINE_PACKAGE_TEST_ROOT"] = str(root_link)
        try:
            with self.assertRaises(builder.BuilderError) as caught:
                fixture.build()
            self.assertEqual(caught.exception.code, "SYMLINK_REJECTED")
        finally:
            os.environ["MARKETMATCH_OFFLINE_PACKAGE_TEST_ROOT"] = original_root
            root_link.unlink()

        os.link(fixture.database, fixture.destination)
        with self.assertRaises(builder.BuilderError) as caught:
            fixture.build()
        self.assertEqual(caught.exception.code, "DESTINATION_EXISTS")

    def test_source_authorizer_denies_sensitive_reads_and_mutations(self) -> None:
        fixture = self.fixture()
        transform = builder.load_transform_contract()
        with builder._open_source(
            fixture.database,
            readable=builder._transform_read_allowlist(transform),
            expected_sha256=sha256_path(fixture.database),
        ) as (connection, _metadata, _digest):
            prohibited = (
                "SELECT token_hash FROM api_tokens",
                "DELETE FROM sessions",
                "ATTACH DATABASE ':memory:' AS other",
                "PRAGMA journal_mode=WAL",
            )
            for statement in prohibited:
                with self.subTest(statement=statement):
                    with self.assertRaises(sqlite3.DatabaseError):
                        connection.execute(statement).fetchall()

    def test_sensitive_registry_keys_and_rejected_transform_values_fail_closed(self) -> None:
        fixture = self.fixture()
        owner_path = fixture.source / "owners.synthetic.json"
        owners = json.loads(owner_path.read_text())
        owners["password_hash"] = fixture.canaries["password"]
        owner_path.write_text(json.dumps(owners, sort_keys=True) + "\n")
        self.assert_builder_error(fixture, "OWNER_REGISTRY_INVALID")

        fixture = self.fixture()
        with closing(sqlite3.connect(fixture.database)) as connection:
            connection.execute(
                "UPDATE marketmatch_calls SET evidence_id='unapproved' WHERE id='call-source-id'"
            )
            connection.commit()
        fixture.refresh_database_hash()
        self.assert_builder_error(fixture, "REJECTED_VALUE_PRESENT")

    def test_concurrent_source_metadata_change_is_detected_and_unsealed(self) -> None:
        fixture = self.fixture()
        original = builder._build_staging

        def mutate_after_staging(*args: object, **kwargs: object):
            result = original(*args, **kwargs)
            owner_path = fixture.source / "owners.synthetic.json"
            owner_path.write_text(owner_path.read_text() + " ")
            return result

        builder._build_staging = mutate_after_staging
        try:
            self.assert_builder_error(fixture, "SOURCE_CHANGED_DURING_BUILD")
        finally:
            builder._build_staging = original

    def test_failed_build_never_looks_sealed_or_valid(self) -> None:
        fixture = self.fixture()
        with closing(sqlite3.connect(fixture.database)) as connection:
            connection.execute("CREATE TABLE unexpected_table(id TEXT)")
            connection.commit()
        fixture.refresh_database_hash()
        self.assert_builder_error(fixture, "UNKNOWN_SOURCE_TABLE")
        self.assertFalse((fixture.destination / "package-manifest.json").exists())

        fixture = self.fixture()
        original_verify = builder._verify_sealed_tree

        def fail_post_seal(
            package: builder.DestinationTree, *, expected_manifest_sha256: str
        ) -> dict[str, object]:
            del package, expected_manifest_sha256
            raise builder.BuilderError("SYNTHETIC_POST_SEAL_FAILURE")

        builder._verify_sealed_tree = fail_post_seal
        try:
            self.assert_builder_error(fixture, "SYNTHETIC_POST_SEAL_FAILURE")
        finally:
            builder._verify_sealed_tree = original_verify

    def test_package_tree_and_canonical_manifest_digests_verify(self) -> None:
        fixture = self.fixture()
        report = fixture.build()
        verified = builder.verify_sealed_package(
            fixture.destination,
            expected_manifest_sha256=report["package_manifest_sha256"],
        )
        self.assertTrue(verified["verified"])
        self.assertEqual(report["package_manifest_sha256"], verified["package_manifest_sha256"])
        self.assertEqual(report["package_tree_sha256"], verified["package_tree_sha256"])
        package_path = fixture.destination / "package-manifest.json"
        package = json.loads(package_path.read_text())
        self.assertEqual(package["canonical_sha256"], canonical_digest(package))
        placeholder = next((fixture.destination / "artifacts").rglob("*.placeholder"))
        placeholder.chmod(0o600)
        placeholder.write_bytes(b"tamper")
        with self.assertRaises(builder.BuilderError) as caught:
            builder.verify_sealed_package(
                fixture.destination,
                expected_manifest_sha256=report["package_manifest_sha256"],
            )
        self.assertEqual(caught.exception.code, "PACKAGE_TREE_DIGEST_MISMATCH")

    def test_seal_verifier_pins_incomplete_fixture_only_policy(self) -> None:
        fixture = self.fixture()
        report = fixture.build()
        package_path = fixture.destination / "package-manifest.json"
        package = json.loads(package_path.read_text())
        package["authorization"] = "AUTHORIZED"
        package["canonical_sha256"] = canonical_digest(package)
        package_path.write_text(json.dumps(package, sort_keys=True) + "\n")
        with self.assertRaises(builder.BuilderError) as caught:
            builder.verify_sealed_package(
                fixture.destination,
                expected_manifest_sha256=package["canonical_sha256"],
            )
        self.assertEqual(caught.exception.code, "PACKAGE_POLICY_MISMATCH")

        with self.assertRaises(TypeError):
            builder.verify_sealed_package(fixture.destination)
        self.assertNotEqual(report["package_manifest_sha256"], package["canonical_sha256"])

    def test_recomputed_payload_and_manifest_fail_without_original_trusted_digest(self) -> None:
        fixture = self.fixture()
        report = fixture.build()
        owners_path = fixture.destination / "manifests" / "owners.sanitized.json"
        owners = json.loads(owners_path.read_text())
        owners["active"].append("owner_recomputed_payload")
        owners_path.write_text(json.dumps(owners, sort_keys=True) + "\n")
        tree = builder.DestinationTree.open_existing(fixture.destination)
        try:
            entries = [
                entry
                for entry in tree.entries()
                if entry["path"] != "package-manifest.json"
            ]
        finally:
            tree.close()
        package_path = fixture.destination / "package-manifest.json"
        package = json.loads(package_path.read_text())
        package["tree_entries"] = entries
        package["package_tree_sha256"] = builder._tree_digest(entries)
        package["canonical_sha256"] = canonical_digest(package)
        package_path.write_text(json.dumps(package, sort_keys=True) + "\n")
        with self.assertRaises(builder.BuilderError) as caught:
            builder.verify_sealed_package(
                fixture.destination,
                expected_manifest_sha256=report["package_manifest_sha256"],
            )
        self.assertEqual(caught.exception.code, "PACKAGE_MANIFEST_DIGEST_MISMATCH")

    def test_all_prohibited_canaries_are_absent_from_every_output_and_sqlite_cell(self) -> None:
        fixture = self.fixture()
        stdout = io.StringIO()
        stderr = io.StringIO()
        code = builder.main(
            [
                "--fixture-only",
                "--source",
                str(fixture.source_rel),
                "--destination",
                str(fixture.destination_rel),
            ],
            stdout=stdout,
            stderr=stderr,
        )
        self.assertEqual(code, 0, stderr.getvalue())
        blobs = [stdout.getvalue().encode(), stderr.getvalue().encode()]
        names = []
        for path in fixture.destination.rglob("*"):
            names.append(str(path.relative_to(fixture.destination)))
            if path.is_file():
                blobs.append(path.read_bytes())
        with closing(
            sqlite3.connect(fixture.destination / "database" / "main.sqlite")
        ) as connection:
            tables = [
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                )
            ]
            for table in tables:
                if table.startswith("chat_messages_fts_"):
                    continue
                try:
                    rows = connection.execute(f'SELECT * FROM "{table}"').fetchall()
                except sqlite3.DatabaseError:
                    continue
                blobs.append(repr(rows).encode())
        haystack = b"\n".join(blobs) + "\n".join(names).encode()
        for label, canary in fixture.canaries.items():
            with self.subTest(canary=label):
                self.assertNotIn(canary.encode(), haystack)
        self.assertNotIn(str(fixture.source.resolve()).encode(), haystack)
        for source_identifier in (
            "AliceFixture",
            "RetiredFixture",
            "LegacyFixture",
            "session-source-id",
            "document-source-id",
            "call-source-id",
            "video-source-id",
            "upload-audio-source-id",
            "upload-video-source-id",
            "workflow-source-id",
            "https://fixture.invalid",
        ):
            self.assertNotIn(source_identifier.encode(), haystack)

    def test_no_forbidden_application_or_dependency_imports(self) -> None:
        imported_roots = {name.split(".", 1)[0] for name in sys.modules}
        self.assertTrue(PROHIBITED_MODULE_ROOTS.isdisjoint(imported_roots))
        source = BUILDER_PATH.read_text(encoding="utf-8")
        for prohibited in PROHIBITED_MODULE_ROOTS:
            self.assertNotIn(f"import {prohibited}", source)
            self.assertNotIn(f"from {prohibited}", source)

    def test_launcher_refuses_inherited_runtime_variables(self) -> None:
        environment = os.environ.copy()
        environment["DATABASE_URL"] = "sqlite:///must-not-be-opened.sqlite"
        completed = subprocess.run(
            [str(REPO_ROOT / "tests" / "run_offline_package_tests.sh")],
            cwd=REPO_ROOT,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 64)
        self.assertIn("refusing inherited runtime path variable: DATABASE_URL", completed.stderr)
        self.assertNotIn("must-not-be-opened", completed.stdout + completed.stderr)

    def test_phase3a_inspector_rejects_package_without_future_policy(self) -> None:
        fixture = self.fixture()
        fixture.build()
        database = fixture.destination / "database" / "main.sqlite"
        completed = subprocess.run(
            [
                sys.executable,
                "-I",
                "-B",
                str(REPO_ROOT / "scripts" / "inspect_marketmatch_offline.py"),
                "--offline-root",
                str(fixture.destination),
                "--database",
                "database/main.sqlite",
                "--expected-sha256",
                sha256_path(database),
            ],
            cwd=REPO_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(completed.returncode, 0)
        report = json.loads(completed.stdout)
        self.assertTrue(
            any(finding.get("code") == "real_database_use_blocked" for finding in report["findings"])
        )


if __name__ == "__main__":
    unittest.main()
