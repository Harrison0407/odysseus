"""Disposable standard-library tests for the offline MarketMatch inspector."""

from __future__ import annotations

import ast
import copy
import hashlib
import importlib.util
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
import subprocess
from pathlib import Path
from unittest import mock


REPOSITORY = Path(__file__).resolve().parents[2]
SCRIPT = REPOSITORY / "scripts" / "inspect_marketmatch_offline.py"
MANIFEST_PATH = (
    REPOSITORY
    / "scripts"
    / "schema_contracts"
    / "combined_9844a2f_6197984.json"
)
PHASE2_COMMIT = "6e2d0e8140eb626287454fe6bdac305740f90b8b"
TEST_PARENT = Path(os.environ["OFFLINE_INSPECTOR_TEST_ROOT"]).resolve()
APPLICATION_PREFIXES = (
    "app",
    "core",
    "routes",
    "services",
    "src",
    "sqlalchemy",
    "chromadb",
)


def application_modules() -> set[str]:
    return {
        name
        for name in sys.modules
        if any(name == prefix or name.startswith(prefix + ".") for prefix in APPLICATION_PREFIXES)
    }


MODULES_BEFORE_IMPORT = application_modules()
spec = importlib.util.spec_from_file_location("marketmatch_offline_inspector", SCRIPT)
if spec is None or spec.loader is None:
    raise RuntimeError("unable to load offline inspector")
inspector = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = inspector
spec.loader.exec_module(inspector)
MODULES_AFTER_IMPORT = application_modules()


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def static_source_contracts(source: str, wanted: set[str] | None = None) -> dict[str, dict]:
    """Extract source declarations without importing application or ORM code."""

    result: dict[str, dict] = {}
    for class_node in (
        node for node in ast.parse(source).body if isinstance(node, ast.ClassDef)
    ):
        table = None
        for node in class_node.body:
            if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == "__tablename__"
                for target in node.targets
            ):
                table = ast.literal_eval(node.value)
        if not table or (wanted is not None and table not in wanted):
            continue
        columns = []
        foreign_keys = []
        indexes = {}
        unique_columns = set()
        for node in class_node.body:
            if not (
                isinstance(node, ast.Assign)
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
            ):
                continue
            attribute = node.targets[0].id
            if (
                isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Name)
                and node.value.func.id == "Column"
            ):
                arguments = list(node.value.args)
                name = attribute
                if (
                    arguments
                    and isinstance(arguments[0], ast.Constant)
                    and isinstance(arguments[0].value, str)
                ):
                    name = arguments.pop(0).value
                type_node = next(
                    (
                        argument
                        for argument in arguments
                        if not (
                            isinstance(argument, ast.Call)
                            and isinstance(argument.func, ast.Name)
                            and argument.func.id == "ForeignKey"
                        )
                    ),
                    None,
                )
                expression = ast.unparse(type_node)
                if expression.startswith("String("):
                    declared_type = "VARCHAR(" + expression[len("String(") :]
                else:
                    declared_type = {
                        "String": "VARCHAR",
                        "Text": "TEXT",
                        "EncryptedText": "TEXT",
                        "Boolean": "BOOLEAN",
                        "DateTime": "DATETIME",
                        "Integer": "INTEGER",
                        "JSON": "JSON",
                    }[expression]
                keywords = {
                    item.arg: ast.literal_eval(item.value)
                    for item in node.value.keywords
                    if item.arg in {"nullable", "primary_key", "index", "unique"}
                }
                primary = bool(keywords.get("primary_key", False))
                columns.append(
                    (
                        name,
                        declared_type,
                        1 if primary or keywords.get("nullable") is False else 0,
                        None,
                        1 if primary else 0,
                        0,
                    )
                )
                if keywords.get("index", False):
                    indexes[f"ix_{table}_{name}"] = ((name,), False)
                if keywords.get("unique", False):
                    unique_columns.add(name)
                for argument in arguments:
                    if not (
                        isinstance(argument, ast.Call)
                        and isinstance(argument.func, ast.Name)
                        and argument.func.id == "ForeignKey"
                    ):
                        continue
                    target_table, target_column = ast.literal_eval(
                        argument.args[0]
                    ).split(".", 1)
                    options = {
                        item.arg: ast.literal_eval(item.value)
                        for item in argument.keywords
                    }
                    foreign_keys.append(
                        (
                            target_table,
                            name,
                            target_column,
                            options.get("onupdate", "NO ACTION"),
                            options.get("ondelete", "NO ACTION"),
                        )
                    )
            elif attribute == "__table_args__" and isinstance(node.value, ast.Tuple):
                for call in node.value.elts:
                    if not (
                        isinstance(call, ast.Call)
                        and isinstance(call.func, ast.Name)
                        and call.func.id == "Index"
                    ):
                        continue
                    index_name = ast.literal_eval(call.args[0])
                    index_columns = tuple(
                        ast.literal_eval(argument) for argument in call.args[1:]
                    )
                    unique = next(
                        (
                            ast.literal_eval(item.value)
                            for item in call.keywords
                            if item.arg == "unique"
                        ),
                        False,
                    )
                    indexes[index_name] = (index_columns, bool(unique))
        if any(
            isinstance(base, ast.Name) and base.id == "TimestampMixin"
            for base in class_node.bases
        ):
            columns.extend(
                (
                    ("created_at", "DATETIME", 1, None, 0, 0),
                    ("updated_at", "DATETIME", 1, None, 0, 0),
                )
            )
        result[table] = {
            "columns": columns,
            "foreign_keys": set(foreign_keys),
            "indexes": indexes,
            "unique_columns": unique_columns,
        }
    return result


class OfflineInspectorTests(unittest.TestCase):
    maxDiff = None

    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest = inspector.load_manifest(MANIFEST_PATH)

    def setUp(self) -> None:
        self.root = Path(
            tempfile.mkdtemp(prefix="case-", dir=os.fspath(TEST_PARENT))
        ).resolve()
        self.database = self.root / "fixture.db"
        self.artifacts = self.root / "artifacts"
        self.artifacts.mkdir()

    def tearDown(self) -> None:
        shutil.rmtree(self.root)

    def prepare_database(
        self,
        *,
        include_upstream: bool = True,
        marketmatch_tables: tuple[str, ...] = ("marketmatch_calls", "marketmatch_videos"),
        table_sql_overrides: dict[str, str] | None = None,
        index_sql_overrides: dict[str, str] | None = None,
        skipped_indexes: set[str] | None = None,
        fts: str = "absent",
        extra_sql: tuple[str, ...] = (),
    ) -> None:
        table_sql_overrides = table_sql_overrides or {}
        index_sql_overrides = index_sql_overrides or {}
        skipped_indexes = skipped_indexes or set()
        if self.database.exists():
            self.database.unlink()
        selected = []
        if include_upstream:
            selected.extend(self.manifest["expected_upstream_tables"])
        selected.extend(marketmatch_tables)
        connection = sqlite3.connect(self.database)
        try:
            connection.execute("PRAGMA foreign_keys=ON")
            for table in selected:
                contract = self.manifest["tables"][table]
                connection.execute(
                    table_sql_overrides.get(table, contract["raw_sql_variants"][0])
                )
            for table in selected:
                for index in self.manifest["tables"][table]["indexes"]:
                    name = index["name"]
                    raw_sql = index_sql_overrides.get(
                        name, index["raw_sql_variants"][0]
                    )
                    if name not in skipped_indexes and raw_sql is not None:
                        connection.execute(raw_sql)
            if fts in ("complete", "partial", "unknown"):
                virtual = self.manifest["fts_contract"]["objects"][
                    "chat_messages_fts"
                ]["raw_sql_variants"][0]
                if fts == "unknown":
                    virtual = "CREATE VIRTUAL TABLE chat_messages_fts USING fts5(content)"
                connection.execute(virtual)
                if fts in ("complete", "unknown"):
                    for name in (
                        "chat_messages_fts_ai",
                        "chat_messages_fts_ad",
                        "chat_messages_fts_au",
                    ):
                        connection.execute(
                            self.manifest["fts_contract"]["objects"][name][
                                "raw_sql_variants"
                            ][0]
                        )
            for statement in extra_sql:
                connection.execute(statement)
            connection.commit()
        finally:
            connection.close()

    def invoke(
        self,
        *,
        database: str = "fixture.db",
        expected_hash: str | None = None,
        owner_registry: str | None = None,
        upload_manifest: str | None = None,
        rag_manifest: str | None = None,
        artifact_roots: tuple[str, ...] = ("artifacts",),
        extra: tuple[str, ...] = (),
    ) -> tuple[int, dict]:
        target = Path(database)
        hash_value = expected_hash
        if hash_value is None:
            candidate = self.root / target
            hash_value = file_hash(candidate) if candidate.is_file() else "0" * 64
        arguments = [
            "--offline-root",
            os.fspath(self.root),
            "--database",
            database,
            "--expected-sha256",
            hash_value,
            "--controlled-disposable-fixture",
        ]
        if owner_registry:
            arguments.extend(("--owner-registry", owner_registry))
        if upload_manifest:
            arguments.extend(("--upload-manifest", upload_manifest))
        if rag_manifest:
            arguments.extend(("--rag-manifest", rag_manifest))
        for artifact_root in artifact_roots:
            arguments.extend(("--artifact-root", artifact_root))
        arguments.extend(extra)
        return inspector.run(arguments)

    def write_json(self, name: str, value: object) -> str:
        path = self.root / name
        path.write_text(json.dumps(value), encoding="utf-8")
        return name

    def raw_table(self, table: str) -> str:
        return self.manifest["tables"][table]["raw_sql_variants"][0]

    def finding_codes(self, report: dict) -> list[str]:
        return [item["code"] for item in report["findings"]]

    def test_no_application_imports(self) -> None:
        self.assertEqual(MODULES_BEFORE_IMPORT, set())
        self.assertEqual(MODULES_AFTER_IMPORT, set())
        self.assertEqual(application_modules(), set())
        imported_roots = set()
        for node in ast.walk(ast.parse(SCRIPT.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                imported_roots.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_roots.add(node.module.split(".", 1)[0])
        self.assertEqual(
            imported_roots - set(sys.stdlib_module_names) - {"__future__"},
            set(),
        )

    def test_manifest_sources_digest_and_fixture_only_policy(self) -> None:
        self.assertEqual(
            self.manifest["source_snapshots"]["upstream"],
            "9844a2f9a1996b8c8135a9e7bbde6a72f41df5ed",
        )
        self.assertEqual(
            self.manifest["source_snapshots"]["marketmatch_checkpoint"],
            "6197984ccec995b630006253cf6eb62c43908d5f",
        )
        self.assertEqual(
            self.manifest["usage_policy"]["real_database_use"],
            "BLOCKED_PENDING_RAW_SQL_REVIEW",
        )
        digest = hashlib.sha256(
            inspector.canonical_manifest_bytes(self.manifest)
        ).hexdigest()
        self.assertEqual(digest, self.manifest["canonical_sha256"])

    def test_self_digested_alternate_manifest_is_not_trusted(self) -> None:
        alternate = copy.deepcopy(self.manifest)
        alternate["contract_name"] = "attacker-selected-contract"
        alternate["canonical_sha256"] = hashlib.sha256(
            inspector.canonical_manifest_bytes(alternate)
        ).hexdigest()
        path = self.root / "alternate.json"
        path.write_text(json.dumps(alternate), encoding="utf-8")
        with self.assertRaises(inspector.InspectionError) as caught:
            inspector.load_manifest(path)
        self.assertEqual(caught.exception.code, "manifest_not_pinned")

    def test_manifest_structure_matches_authoritative_source_snapshots(self) -> None:
        upstream_commit = self.manifest["source_snapshots"]["upstream"]
        marketmatch_commit = self.manifest["source_snapshots"][
            "marketmatch_checkpoint"
        ]
        upstream_source = subprocess.run(
            ["git", "show", f"{upstream_commit}:core/database.py"],
            cwd=REPOSITORY,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
        ).stdout
        marketmatch_source = subprocess.run(
            ["git", "show", f"{marketmatch_commit}:core/database.py"],
            cwd=REPOSITORY,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
        ).stdout
        system_tool_source = subprocess.run(
            ["git", "show", f"{upstream_commit}:src/tools/system.py"],
            cwd=REPOSITORY,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
        ).stdout
        middleware_source = subprocess.run(
            ["git", "show", f"{upstream_commit}:core/middleware.py"],
            cwd=REPOSITORY,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
        ).stdout
        phase2_auth_source = subprocess.run(
            ["git", "show", f"{PHASE2_COMMIT}:core/auth.py"],
            cwd=REPOSITORY,
            check=True,
            text=True,
            stdout=subprocess.PIPE,
        ).stdout
        self.assertIn(
            '"CREATE INDEX IF NOT EXISTS ix_sessions_last_message_at "',
            upstream_source,
        )
        self.assertIn(
            '"CREATE UNIQUE INDEX IF NOT EXISTS ix_scheduled_tasks_webhook ON scheduled_tasks(webhook_token)"',
            upstream_source,
        )
        self.assertIn('owner="internal-tool"', system_tool_source)
        self.assertIn('INTERNAL_TOOL_USER = "internal-tool"', middleware_source)
        self.assertIn(
            'RESERVED_USERNAMES = frozenset({INTERNAL_TOOL_USER, "api", "demo", "system"})',
            phase2_auth_source,
        )
        self.assertEqual(
            self.manifest["owner_inventory"]["internal_owner_values"],
            ["api", "demo", "internal-tool", "system"],
        )
        source_contracts = static_source_contracts(upstream_source)
        source_contracts.update(
            static_source_contracts(
                marketmatch_source,
                {"marketmatch_calls", "marketmatch_videos"},
            )
        )
        source_contracts["sessions"]["indexes"]["ix_sessions_last_message_at"] = (
            ("archived", "last_message_at"),
            False,
        )
        source_contracts["scheduled_tasks"]["indexes"][
            "ix_scheduled_tasks_webhook"
        ] = (("webhook_token",), True)
        self.assertEqual(set(source_contracts), set(self.manifest["tables"]))
        self.assertEqual(
            set(static_source_contracts(upstream_source)),
            set(self.manifest["expected_upstream_tables"]),
        )
        for table, source_contract in source_contracts.items():
            with self.subTest(table=table):
                manifest_contract = self.manifest["tables"][table]
                manifest_columns = [
                    (
                        column["name"],
                        column["type"],
                        column["notnull"],
                        column["default"],
                        column["pk"],
                        column["hidden"],
                    )
                    for column in manifest_contract["columns"]
                ]
                self.assertEqual(manifest_columns, source_contract["columns"])
                manifest_foreign_keys = {
                    (
                        foreign_key["table"],
                        foreign_key["from"],
                        foreign_key["to"],
                        foreign_key["on_update"],
                        foreign_key["on_delete"],
                    )
                    for foreign_key in manifest_contract["foreign_keys"]
                }
                self.assertEqual(
                    manifest_foreign_keys, source_contract["foreign_keys"]
                )
                manifest_indexes = {
                    index["name"]: (
                        tuple(
                            term["name"]
                            for term in index["terms"]
                            if term["key"] == 1
                        ),
                        bool(index["unique"]),
                    )
                    for index in manifest_contract["indexes"]
                    if index["origin"] == "c"
                }
                self.assertEqual(manifest_indexes, source_contract["indexes"])
                manifest_unique_columns = {
                    term["name"]
                    for index in manifest_contract["indexes"]
                    if index["origin"] == "u"
                    for term in index["terms"]
                    if term["key"] == 1
                }
                self.assertEqual(
                    manifest_unique_columns, source_contract["unique_columns"]
                )

    def test_exact_structural_match(self) -> None:
        self.prepare_database()
        status, report = self.invoke()
        self.assertEqual(status, 0)
        self.assertEqual(report["aggregate_outcome"], "EXACT_MATCH")
        self.assertNotIn("unknown_raw_table_sql", self.finding_codes(report))

    def test_outcome_precedence_is_exactly_the_approved_order(self) -> None:
        reporter = inspector.Reporter()
        approved_low_to_high = (
            "EXACT_MATCH",
            "TABLE_ABSENT",
            "COMPATIBLE_ADDITIVE_DIFFERENCE",
            "INCOMPATIBLE_INDEXES",
            "INCOMPATIBLE_COLUMNS",
            "INCOMPATIBLE_CONSTRAINTS",
            "UNKNOWN_OR_UNSUPPORTED",
            "READ_ERROR",
        )
        self.assertEqual(
            tuple(sorted(inspector.PRECEDENCE, key=inspector.PRECEDENCE.get)),
            approved_low_to_high,
        )
        for position, outcome in enumerate(approved_low_to_high):
            reporter.add(outcome, f"finding-{position}", "fixture")
            self.assertEqual(reporter.aggregate(), outcome)
        self.assertEqual(len(reporter.findings), len(approved_low_to_high))

    def test_missing_upstream_table_aggregates_to_table_absent(self) -> None:
        self.prepare_database()
        connection = sqlite3.connect(self.database)
        try:
            connection.execute("PRAGMA foreign_keys=OFF")
            connection.execute("DROP TABLE sessions")
            connection.commit()
        finally:
            connection.close()
        _, report = self.invoke()
        self.assertEqual(report["aggregate_outcome"], "TABLE_ABSENT")
        self.assertIn("table_absent", self.finding_codes(report))

    def test_both_marketmatch_tables_absent(self) -> None:
        self.prepare_database(marketmatch_tables=())
        status, report = self.invoke()
        self.assertEqual(status, 0)
        self.assertEqual(
            report["aggregate_outcome"], "COMPATIBLE_ADDITIVE_DIFFERENCE"
        )
        absent = [
            item["subject"]
            for item in report["findings"]
            if item["code"] == "table_absent"
        ]
        self.assertEqual(set(absent), {"marketmatch_calls", "marketmatch_videos"})

    def test_one_marketmatch_table_present_and_one_absent(self) -> None:
        self.prepare_database(marketmatch_tables=("marketmatch_calls",))
        status, report = self.invoke()
        self.assertEqual(status, 0)
        self.assertEqual(
            report["aggregate_outcome"], "COMPATIBLE_ADDITIVE_DIFFERENCE"
        )
        self.assertIn("marketmatch_additive_candidate", self.finding_codes(report))

    def test_missing_extra_renamed_reordered_and_type_changed_columns(self) -> None:
        raw = self.raw_table("marketmatch_calls")
        variants = {
            "missing": raw.replace(', "rag_status" VARCHAR', ""),
            "extra": raw.replace(
                ', PRIMARY KEY ("id")',
                ', "fixture_extra" TEXT, PRIMARY KEY ("id")',
            ),
            "renamed": raw.replace('"rag_status" VARCHAR', '"rag_state" VARCHAR'),
            "reordered": raw.replace(
                '"error_message" TEXT, "rag_status" VARCHAR',
                '"rag_status" VARCHAR, "error_message" TEXT',
            ),
            "type": raw.replace('"rag_status" VARCHAR', '"rag_status" INTEGER'),
        }
        for label, variant in variants.items():
            with self.subTest(label=label):
                self.prepare_database(
                    table_sql_overrides={"marketmatch_calls": variant}
                )
                _, report = self.invoke()
                self.assertIn("column_contract_difference", self.finding_codes(report))
                self.database.unlink()

    def test_nullability_default_and_pk_position_differences(self) -> None:
        raw = self.raw_table("marketmatch_calls")
        variants = {
            "nullability": raw.replace('"language" VARCHAR', '"language" VARCHAR NOT NULL'),
            "default": raw.replace('"language" VARCHAR', '"language" VARCHAR DEFAULT \'en\''),
            "pk": raw.replace(', PRIMARY KEY ("id")', ""),
        }
        for label, variant in variants.items():
            with self.subTest(label=label):
                self.prepare_database(
                    table_sql_overrides={"marketmatch_calls": variant}
                )
                _, report = self.invoke()
                self.assertIn("column_contract_difference", self.finding_codes(report))
                self.database.unlink()

    def test_foreign_key_difference(self) -> None:
        raw = self.raw_table("documents").replace(
            "ON UPDATE NO ACTION ON DELETE SET NULL",
            "ON UPDATE NO ACTION ON DELETE CASCADE",
        )
        self.prepare_database(table_sql_overrides={"documents": raw})
        _, report = self.invoke()
        self.assertIn("foreign_key_difference", self.finding_codes(report))
        self.assertEqual(report["aggregate_outcome"], "UNKNOWN_OR_UNSUPPORTED")

    def test_table_unique_constraint_difference(self) -> None:
        raw = self.raw_table("gallery_images").replace(
            ', UNIQUE ("filename")',
            "",
        )
        self.prepare_database(table_sql_overrides={"gallery_images": raw})
        _, report = self.invoke()
        self.assertIn("missing_constraint_index", self.finding_codes(report))

    def test_missing_and_colliding_indexes(self) -> None:
        name = "ix_marketmatch_calls_owner"
        self.prepare_database(skipped_indexes={name})
        _, report = self.invoke()
        self.assertIn("missing_index", self.finding_codes(report))
        self.database.unlink()

        self.prepare_database(
            skipped_indexes={name},
            extra_sql=(f'CREATE TABLE "{name}" ("id" INTEGER)',),
        )
        _, report = self.invoke()
        self.assertIn("sqlite_namespace_collision", self.finding_codes(report))

    def test_unique_partial_expression_descending_and_reordered_indexes(self) -> None:
        name = "ix_marketmatch_calls_owner_created"
        variants = {
            "unique": 'CREATE UNIQUE INDEX "ix_marketmatch_calls_owner_created" ON "marketmatch_calls" ("owner", "created_at")',
            "partial": 'CREATE INDEX "ix_marketmatch_calls_owner_created" ON "marketmatch_calls" ("owner", "created_at") WHERE "owner" IS NOT NULL',
            "expression": 'CREATE INDEX "ix_marketmatch_calls_owner_created" ON "marketmatch_calls" (lower("owner"), "created_at")',
            "descending": 'CREATE INDEX "ix_marketmatch_calls_owner_created" ON "marketmatch_calls" ("owner" DESC, "created_at")',
            "reordered": 'CREATE INDEX "ix_marketmatch_calls_owner_created" ON "marketmatch_calls" ("created_at", "owner")',
        }
        for label, variant in variants.items():
            with self.subTest(label=label):
                self.prepare_database(index_sql_overrides={name: variant})
                _, report = self.invoke()
                codes = self.finding_codes(report)
                if label == "unique":
                    self.assertIn("index_uniqueness_difference", codes)
                elif label == "partial":
                    self.assertIn("partial_index_difference", codes)
                    self.assertIn("partial_index_predicate_difference", codes)
                else:
                    self.assertIn("index_term_difference", codes)
                self.assertIn("unknown_raw_index_sql", codes)
                self.database.unlink()

    def test_trigger_view_and_table_namespace_collisions(self) -> None:
        scenarios = (
            ("trigger", 'CREATE TRIGGER rogue_trigger AFTER INSERT ON sessions BEGIN SELECT 1; END'),
            ("view", 'CREATE VIEW rogue_view AS SELECT id FROM sessions'),
            (
                "table",
                'CREATE TABLE rogue_table_namespace (id INTEGER)',
            ),
        )
        for label, statement in scenarios:
            with self.subTest(label=label):
                self.prepare_database(extra_sql=(statement,))
                _, report = self.invoke()
                self.assertIn("unexpected_sqlite_object", self.finding_codes(report))
                self.database.unlink()

        self.prepare_database(
            marketmatch_tables=("marketmatch_videos",),
            extra_sql=("CREATE VIEW marketmatch_calls AS SELECT id FROM sessions",),
        )
        _, report = self.invoke()
        self.assertIn("table_namespace_collision", self.finding_codes(report))

    def test_complete_absent_and_partial_fts_groups(self) -> None:
        for state, aggregate, code in (
            ("absent", "EXACT_MATCH", None),
            ("complete", "EXACT_MATCH", None),
            ("partial", "INCOMPATIBLE_CONSTRAINTS", "partial_fts_group"),
        ):
            with self.subTest(state=state):
                self.prepare_database(fts=state)
                _, report = self.invoke()
                self.assertEqual(report["aggregate_outcome"], aggregate)
                if code:
                    self.assertIn(code, self.finding_codes(report))
                self.database.unlink()

    def test_unknown_raw_sql_variant(self) -> None:
        raw = self.raw_table("marketmatch_calls").replace(
            'CREATE TABLE "marketmatch_calls"',
            "CREATE TABLE marketmatch_calls",
        )
        self.prepare_database(table_sql_overrides={"marketmatch_calls": raw})
        _, report = self.invoke()
        self.assertIn("unknown_raw_table_sql", self.finding_codes(report))
        self.assertNotIn("column_contract_difference", self.finding_codes(report))
        self.assertEqual(report["aggregate_outcome"], "UNKNOWN_OR_UNSUPPORTED")

    def test_unknown_virtual_table_layout(self) -> None:
        self.prepare_database(fts="unknown")
        _, report = self.invoke()
        self.assertIn("unknown_fts_raw_sql", self.finding_codes(report))
        self.assertEqual(report["aggregate_outcome"], "UNKNOWN_OR_UNSUPPORTED")

    def test_absolute_and_traversal_rejection(self) -> None:
        self.prepare_database()
        for value, code in (
            (os.fspath(self.database), "absolute_path_rejected"),
            ("../fixture.db", "traversal_rejected"),
        ):
            with self.subTest(value=value):
                status, report = self.invoke(database=value)
                self.assertNotEqual(status, 0)
                self.assertIn(code, self.finding_codes(report))

    def test_symlinked_database_root_component_and_metadata_rejection(self) -> None:
        self.prepare_database()
        link = self.root / "database-link.db"
        link.symlink_to(self.database.name)
        _, report = self.invoke(database=link.name, expected_hash=file_hash(self.database))
        self.assertIn("symlink_rejected", self.finding_codes(report))

        registry = self.write_json(
            "owners.sanitized.json", {key: [] for key in inspector.OWNER_REGISTRY_KEYS}
        )
        registry_link = self.root / "owners-link.json"
        registry_link.symlink_to(registry)
        _, report = self.invoke(owner_registry=registry_link.name)
        self.assertIn("symlink_rejected", self.finding_codes(report))

        outside_link = self.root.parent / (self.root.name + "-link")
        outside_link.symlink_to(self.root, target_is_directory=True)
        try:
            arguments = [
                "--offline-root",
                os.fspath(outside_link),
                "--database",
                "fixture.db",
                "--expected-sha256",
                file_hash(self.database),
                "--controlled-disposable-fixture",
            ]
            _, report = inspector.run(arguments)
            self.assertIn("symlink_rejected", self.finding_codes(report))
        finally:
            outside_link.unlink()

    def test_hard_link_rejection(self) -> None:
        self.prepare_database()
        hardlink = self.root / "hardlink.db"
        os.link(self.database, hardlink)
        _, report = self.invoke()
        self.assertIn("hardlink_rejected", self.finding_codes(report))

    def test_sidecar_rejection(self) -> None:
        self.prepare_database()
        for suffix in inspector.SIDECAR_SUFFIXES:
            with self.subTest(suffix=suffix):
                sidecar = Path(os.fspath(self.database) + suffix)
                sidecar.write_bytes(b"fixture")
                _, report = self.invoke()
                self.assertIn("sidecar_rejected", self.finding_codes(report))
                sidecar.unlink()

    def test_hash_mismatch(self) -> None:
        self.prepare_database()
        status, report = self.invoke(expected_hash="f" * 64)
        self.assertNotEqual(status, 0)
        self.assertIn("hash_mismatch", self.finding_codes(report))

    def test_inode_replacement_is_detected(self) -> None:
        self.prepare_database()
        replacement = self.root / "replacement.db"
        shutil.copyfile(self.database, replacement)
        original_graph_checks = inspector.graph_checks

        def replace_during_inspection(*args, **kwargs):
            os.replace(replacement, self.database)
            return original_graph_checks(*args, **kwargs)

        reporter = inspector.Reporter()
        with mock.patch.object(
            inspector, "graph_checks", side_effect=replace_during_inspection
        ):
            with self.assertRaises(inspector.InspectionError) as caught:
                inspector.inspect(
                    self.database,
                    file_hash(self.database),
                    self.manifest,
                    {key: [] for key in inspector.OWNER_REGISTRY_KEYS},
                    [],
                    [],
                    self.root,
                    [self.artifacts],
                    reporter,
                )
        self.assertEqual(caught.exception.code, "non_mutation_check_failed")

    def test_concurrent_schema_metadata_change_is_detected(self) -> None:
        self.prepare_database()
        original_inventory = inspector.object_inventory
        calls = 0

        def changing_inventory(connection):
            nonlocal calls
            calls += 1
            result = original_inventory(connection)
            if calls >= 3:
                result = copy.deepcopy(result)
                result[0]["sql"] = (result[0]["sql"] or "") + " "
            return result

        reporter = inspector.Reporter()
        with mock.patch.object(
            inspector, "object_inventory", side_effect=changing_inventory
        ):
            with self.assertRaises(inspector.InspectionError) as caught:
                inspector.inspect(
                    self.database,
                    file_hash(self.database),
                    self.manifest,
                    {key: [] for key in inspector.OWNER_REGISTRY_KEYS},
                    [],
                    [],
                    self.root,
                    [self.artifacts],
                    reporter,
                )
        self.assertEqual(caught.exception.code, "concurrent_schema_change")

    def test_authorizer_rejects_prohibited_operations(self) -> None:
        self.prepare_database()
        connection = inspector.open_read_only(self.database, self.manifest)
        try:
            databases = connection.execute("PRAGMA database_list").fetchall()
            self.assertEqual(len(databases), 1)
            self.assertEqual(Path(databases[0][2]).resolve(), self.database.resolve())
            prohibited = (
                "CREATE TABLE prohibited(id INTEGER)",
                "DELETE FROM sessions",
                "PRAGMA query_only=OFF",
                "ATTACH DATABASE ':memory:' AS prohibited",
                "ANALYZE",
            )
            for statement in prohibited:
                with self.subTest(statement=statement):
                    with self.assertRaises(sqlite3.DatabaseError):
                        connection.execute(statement)
            for sensitive_select in (
                "SELECT content FROM chat_messages",
                "SELECT current_content FROM documents",
                "SELECT token_hash FROM api_tokens",
                "SELECT data_png FROM signatures",
            ):
                with self.subTest(sensitive_select=sensitive_select):
                    with self.assertRaises(sqlite3.DatabaseError):
                        connection.execute(sensitive_select)
            self.assertEqual(
                connection.execute("SELECT id, owner FROM documents").fetchall(),
                [],
            )
        finally:
            connection.close()

    def test_sqlite_without_immutable_support_is_rejected(self) -> None:
        self.prepare_database()
        with mock.patch.object(inspector.sqlite3, "sqlite_version_info", (3, 21, 0)):
            with self.assertRaises(inspector.InspectionError) as caught:
                inspector.open_read_only(self.database, self.manifest)
        self.assertEqual(caught.exception.code, "sqlite_immutable_unsupported")

    def test_new_sidecar_created_during_inspection_is_detected(self) -> None:
        self.prepare_database()
        original_graph_checks = inspector.graph_checks

        def create_sidecar(*args, **kwargs):
            result = original_graph_checks(*args, **kwargs)
            Path(os.fspath(self.database) + "-wal").write_bytes(b"fixture-sidecar")
            return result

        reporter = inspector.Reporter()
        with mock.patch.object(
            inspector, "graph_checks", side_effect=create_sidecar
        ):
            with self.assertRaises(inspector.InspectionError) as caught:
                inspector.inspect(
                    self.database,
                    file_hash(self.database),
                    self.manifest,
                    {key: [] for key in inspector.OWNER_REGISTRY_KEYS},
                    [],
                    [],
                    self.root,
                    [self.artifacts],
                    reporter,
                )
        self.assertEqual(caught.exception.code, "sidecar_rejected")

    def test_sensitive_key_and_unexpected_key_rejection(self) -> None:
        self.prepare_database()
        sensitive = self.write_json(
            "owners.sanitized.json",
            {
                "active": [],
                "retired": [],
                "legacy_approved": [],
                "internal": [],
                "password_hash": "prohibited",
            },
        )
        _, report = self.invoke(owner_registry=sensitive)
        self.assertIn("sensitive_key_rejected", self.finding_codes(report))

        unexpected = self.write_json(
            "uploads.sanitized.json",
            [
                {
                    "upload_id": "upload-1",
                    "owner": "owner-a",
                    "approved_relative_path_token": "artifacts/a.bin",
                    "filename": "prohibited",
                }
            ],
        )
        _, report = self.invoke(upload_manifest=unexpected)
        self.assertIn("unexpected_key", self.finding_codes(report))

    def test_unsanitized_input_filename_is_rejected_before_parsing(self) -> None:
        self.prepare_database()
        raw_name = "auth.json"
        (self.root / raw_name).write_text("this is deliberately not JSON", encoding="utf-8")
        _, report = self.invoke(owner_registry=raw_name)
        self.assertIn("unsanitized_filename_rejected", self.finding_codes(report))

    def test_concurrent_sanitized_metadata_change_is_detected(self) -> None:
        self.prepare_database()
        registry_name = self.write_json(
            "owners.sanitized.json",
            {
                "active": [],
                "retired": [],
                "legacy_approved": [],
                "internal": [],
            },
        )
        original_inspect = inspector.inspect

        def mutate_metadata_after_inspection(*args, **kwargs):
            result = original_inspect(*args, **kwargs)
            (self.root / registry_name).write_text(
                json.dumps(
                    {
                        "active": ["changed"],
                        "retired": [],
                        "legacy_approved": [],
                        "internal": [],
                    }
                ),
                encoding="utf-8",
            )
            return result

        with mock.patch.object(
            inspector, "inspect", side_effect=mutate_metadata_after_inspection
        ):
            _, report = self.invoke(owner_registry=registry_name)
        self.assertIn(
            "concurrent_sanitized_metadata_change", self.finding_codes(report)
        )

    def insert_document(self, identifier: str, owner: str) -> None:
        connection = sqlite3.connect(self.database)
        try:
            connection.execute(
                "INSERT INTO documents "
                "(id,title,current_content,owner,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?)",
                (identifier, "fixture", "", owner, "2026-01-01", "2026-01-01"),
            )
            connection.commit()
        finally:
            connection.close()

    def insert_call(
        self,
        identifier: str,
        owner: str,
        upload_id: str | None,
        document_id: str | None,
        summary_document_id: str | None,
        stored_path: str,
        transcript_path: str | None = None,
        summary_path: str | None = None,
        rag_path: str | None = None,
    ) -> None:
        connection = sqlite3.connect(self.database)
        try:
            connection.execute(
                "INSERT INTO marketmatch_calls "
                "(id,owner,original_filename,stored_audio_path,transcript_path,"
                "summary_path,rag_markdown_path,status,upload_id,document_id,"
                "summary_document_id,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    identifier,
                    owner,
                    "fixture.bin",
                    stored_path,
                    transcript_path,
                    summary_path,
                    rag_path,
                    "uploaded",
                    upload_id,
                    document_id,
                    summary_document_id,
                    "2026-01-01",
                    "2026-01-01",
                ),
            )
            connection.commit()
        finally:
            connection.close()

    def test_owner_classification_and_registry_collision(self) -> None:
        self.prepare_database()
        owners = {
            "active": ["ActiveUser", "CollisionUser"],
            "retired": ["RetiredUser", "collisionuser"],
            "legacy_approved": ["LegacyUser"],
            "internal": ["internal-tool"],
        }
        registry_name = self.write_json("owners.sanitized.json", owners)
        for identifier, owner in (
            ("doc-active", " activeuser "),
            ("doc-retired", "RetiredUser"),
            ("doc-legacy", "LegacyUser"),
            ("doc-internal", "internal-tool"),
            ("doc-unknown", "UnknownUser"),
            ("doc-empty", ""),
            ("doc-collision", "COLLISIONUSER"),
        ):
            self.insert_document(identifier, owner)
        _, report = self.invoke(owner_registry=registry_name)
        codes = self.finding_codes(report)
        self.assertIn("owner_registry_collision", codes)
        classifications = {
            item["details"].get("classification")
            for item in report["findings"] + report["information"]
            if item["code"] == "owner_classification"
        }
        self.assertEqual(
            classifications,
            {
                "retired-owner",
                "legacy-owner",
                "internal-owner",
                "unknown-owner",
                "empty-owner",
                "collision",
            },
        )

    def test_same_category_case_normalization_collision(self) -> None:
        self.prepare_database()
        registry = self.write_json(
            "owners.sanitized.json",
            {
                "active": ["CaseOwner", " caseowner "],
                "retired": [],
                "legacy_approved": [],
                "internal": [],
            },
        )
        _, report = self.invoke(owner_registry=registry)
        self.assertIn("owner_registry_collision", self.finding_codes(report))

    def test_unapproved_internal_owner_sentinel_is_rejected(self) -> None:
        self.prepare_database()
        registry = self.write_json(
            "owners.sanitized.json",
            {
                "active": [],
                "retired": [],
                "legacy_approved": [],
                "internal": ["operator-selected-bypass"],
            },
        )
        _, report = self.invoke(owner_registry=registry)
        self.assertIn("unapproved_internal_owner", self.finding_codes(report))

    def test_internal_owners_cannot_be_smuggled_into_active_registry(self) -> None:
        self.prepare_database()
        for sentinel in ("internal-tool", "api", "demo", "system"):
            with self.subTest(sentinel=sentinel):
                registry = self.write_json(
                    f"owners-{sentinel}.sanitized.json",
                    {
                        "active": [sentinel],
                        "retired": [],
                        "legacy_approved": [],
                        "internal": [],
                    },
                )
                _, report = self.invoke(owner_registry=registry)
                self.assertIn(
                    "internal_owner_category_mismatch", self.finding_codes(report)
                )

    def test_complete_internal_owner_policy_is_derived_when_omitted(self) -> None:
        self.prepare_database()
        self._write_artifact("artifacts/api.bin")
        self.insert_call(
            "call-api", "api", "upload-demo", None, None, "artifacts/api.bin"
        )
        registry = self.write_json(
            "owners.sanitized.json",
            {
                "active": [],
                "retired": [],
                "legacy_approved": [],
                "internal": [],
            },
        )
        uploads = self.write_json(
            "uploads.sanitized.json",
            [
                {
                    "upload_id": "upload-demo",
                    "owner": "demo",
                    "approved_relative_path_token": "artifacts/api.bin",
                }
            ],
        )
        rag = self.write_json(
            "rag.sanitized.json",
            [
                {
                    "owner": "system",
                    "source_path_token": "artifacts/api.bin",
                    "call_or_video_id": "call-api",
                    "document_ids": [],
                    "upload_id": "upload-demo",
                    "chunk_id": "chunk-system",
                    "collection_or_lane_identifier": "lane-internal",
                }
            ],
        )
        _, report = self.invoke(
            owner_registry=registry,
            upload_manifest=uploads,
            rag_manifest=rag,
        )
        classifications = {
            item["details"].get("classification")
            for item in report["findings"]
            if item["code"] in {
                "owner_classification",
                "sanitized_owner_classification",
            }
        }
        self.assertIn("internal-owner", classifications)
        resolved = inspector.validate_owner_registry(
            {
                "active": [],
                "retired": [],
                "legacy_approved": [],
                "internal": [],
            },
            self.manifest,
        )
        sets, collisions = inspector.owner_sets(resolved)
        for sentinel in ("internal-tool", "api", "demo", "system"):
            with self.subTest(derived=sentinel):
                self.assertEqual(
                    inspector.classify_owner(sentinel, sets, collisions),
                    "internal-owner",
                )

    def test_legacy_and_internal_marketmatch_owners_block(self) -> None:
        self.prepare_database()
        self._write_artifact("artifacts/internal.bin")
        self._write_artifact("artifacts/legacy.bin")
        self.insert_call(
            "call-internal", "internal-tool", None, None, None, "artifacts/internal.bin"
        )
        self.insert_call(
            "call-legacy", "legacy-owner", None, None, None, "artifacts/legacy.bin"
        )
        registry = self.write_json(
            "owners.sanitized.json",
            {
                "active": [],
                "retired": [],
                "legacy_approved": ["legacy-owner"],
                "internal": ["internal-tool"],
            },
        )
        _, report = self.invoke(owner_registry=registry)
        blocking = {
            item["details"].get("classification")
            for item in report["findings"]
            if item["code"] == "owner_classification"
            and item["details"].get("table") == "marketmatch_calls"
        }
        self.assertEqual(blocking, {"legacy-owner", "internal-owner"})

    def test_sanitized_upload_and_rag_owners_are_classified(self) -> None:
        self.prepare_database()
        registry = self.write_json(
            "owners.sanitized.json",
            {
                "active": [],
                "retired": ["retired-owner"],
                "legacy_approved": [],
                "internal": ["internal-tool"],
            },
        )
        uploads = self.write_json(
            "uploads.sanitized.json",
            [
                {
                    "upload_id": "orphan-retired-upload",
                    "owner": "retired-owner",
                    "approved_relative_path_token": "artifacts/missing.bin",
                }
            ],
        )
        rag = self.write_json(
            "rag.sanitized.json",
            [
                {
                    "owner": "internal-tool",
                    "source_path_token": "artifacts/missing.md",
                    "call_or_video_id": "missing-workflow",
                    "document_ids": [],
                    "upload_id": "orphan-retired-upload",
                    "chunk_id": "internal-chunk",
                    "collection_or_lane_identifier": "internal-lane",
                }
            ],
        )
        _, report = self.invoke(
            owner_registry=registry,
            upload_manifest=uploads,
            rag_manifest=rag,
        )
        classifications = {
            item["details"].get("classification")
            for item in report["findings"] + report["information"]
            if item["code"] == "sanitized_owner_classification"
        }
        self.assertEqual(classifications, {"retired-owner", "internal-owner"})

    def test_output_redacts_raw_owners_and_sensitive_paths(self) -> None:
        self.prepare_database()
        self.insert_document("doc-redact", "OwnerSecret-Example")
        registry = self.write_json(
            "owners.sanitized.json",
            {
                "active": [],
                "retired": [],
                "legacy_approved": [],
                "internal": [],
            },
        )
        _, report = self.invoke(owner_registry=registry)
        encoded = json.dumps(report, sort_keys=True)
        self.assertNotIn("OwnerSecret-Example", encoded)
        self.assertNotIn(os.fspath(self.root), encoded)
        self.assertNotIn("fixture.db", encoded)
        self.assertIn("hmac:owner:", encoded)
        self.assertFalse(report["privacy"]["hmac_key_disclosed"])

        _, invalid_report = inspector.run(
            ["--unexpected", "/sensitive/argument/that/must/not/echo"]
        )
        invalid_encoded = json.dumps(invalid_report, sort_keys=True)
        self.assertIn("invalid_arguments", invalid_encoded)
        self.assertNotIn("sensitive/argument", invalid_encoded)

    def _write_artifact(self, relative: str, data: bytes = b"fixture") -> None:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def test_cross_owner_upload_document_rag_and_path_links(self) -> None:
        self.prepare_database()
        for relative in (
            "artifacts/call.bin",
            "artifacts/transcript.md",
            "artifacts/summary.md",
            "artifacts/rag.md",
        ):
            self._write_artifact(relative)
        self.insert_document("doc-transcript", "owner-b")
        self.insert_document("doc-summary", "owner-b")
        self.insert_call(
            "call-cross-owner",
            "owner-a",
            "upload-cross-owner",
            "doc-transcript",
            "doc-summary",
            "artifacts/call.bin",
            "artifacts/transcript.md",
            "artifacts/summary.md",
            "artifacts/rag.md",
        )
        registry = self.write_json(
            "owners.sanitized.json",
            {
                "active": ["owner-a", "owner-b"],
                "retired": [],
                "legacy_approved": [],
                "internal": [],
            },
        )
        uploads = self.write_json(
            "uploads.sanitized.json",
            [
                {
                    "upload_id": "upload-cross-owner",
                    "owner": "owner-b",
                    "approved_relative_path_token": "artifacts/call.bin",
                }
            ],
        )
        rag = self.write_json(
            "rag.sanitized.json",
            [
                {
                    "owner": "owner-b",
                    "source_path_token": "artifacts/rag.md",
                    "call_or_video_id": "call-cross-owner",
                    "document_ids": ["doc-transcript", "doc-summary"],
                    "upload_id": "upload-cross-owner",
                    "chunk_id": "chunk-1",
                    "collection_or_lane_identifier": "marketmatch-calls",
                }
            ],
        )
        _, report = self.invoke(
            owner_registry=registry,
            upload_manifest=uploads,
            rag_manifest=rag,
        )
        links = [
            item
            for item in report["findings"]
            if item["code"] == "cross_owner_link"
        ]
        self.assertEqual(
            {item["details"]["link_kind"] for item in links},
            {"upload", "document_id", "summary_document_id", "rag"},
        )

    def test_conflicting_path_and_inode_reuse(self) -> None:
        self.prepare_database()
        self._write_artifact("artifacts/shared.bin")
        hardlink = self.root / "artifacts" / "same-inode.bin"
        os.link(self.root / "artifacts" / "shared.bin", hardlink)
        for suffix, owner, path in (
            ("a", "owner-a", "artifacts/shared.bin"),
            ("b", "owner-b", "artifacts/shared.bin"),
            ("c", "owner-c", "artifacts/same-inode.bin"),
        ):
            self.insert_call(
                f"call-{suffix}", owner, f"upload-{suffix}", None, None, path
            )
        registry = self.write_json(
            "owners.sanitized.json",
            {
                "active": ["owner-a", "owner-b", "owner-c"],
                "retired": [],
                "legacy_approved": [],
                "internal": [],
            },
        )
        uploads = self.write_json(
            "uploads.sanitized.json",
            [
                {
                    "upload_id": f"upload-{suffix}",
                    "owner": owner,
                    "approved_relative_path_token": path,
                }
                for suffix, owner, path in (
                    ("a", "owner-a", "artifacts/shared.bin"),
                    ("b", "owner-b", "artifacts/shared.bin"),
                    ("c", "owner-c", "artifacts/same-inode.bin"),
                )
            ],
        )
        _, report = self.invoke(owner_registry=registry, upload_manifest=uploads)
        codes = self.finding_codes(report)
        self.assertIn("conflicting_path_reuse", codes)
        self.assertIn("conflicting_inode_reuse", codes)
        self.assertIn("artifact_hardlink_rejected", codes)

    def test_artifact_symlink_and_non_directory_root_rejection(self) -> None:
        self.prepare_database()
        self._write_artifact("artifacts/target.bin")
        (self.artifacts / "linked.bin").symlink_to("target.bin")
        self.insert_call(
            "call-symlink", "owner-a", None, None, None, "artifacts/linked.bin"
        )
        registry = self.write_json(
            "owners.sanitized.json",
            {
                "active": ["owner-a"],
                "retired": [],
                "legacy_approved": [],
                "internal": [],
            },
        )
        _, report = self.invoke(owner_registry=registry)
        unsafe = [
            item
            for item in report["findings"]
            if item["code"] == "missing_or_unsafe_artifact"
        ]
        self.assertTrue(
            any(item["details"].get("reason") == "symlink_rejected" for item in unsafe)
        )

        artifact_file = self.root / "not-a-directory"
        artifact_file.write_bytes(b"fixture")
        _, report = self.invoke(
            owner_registry=registry,
            artifact_roots=(artifact_file.name,),
        )
        self.assertIn("artifact_root_not_directory", self.finding_codes(report))

    def test_omitted_rag_manifest_is_incomplete_for_indexed_workflow(self) -> None:
        self.prepare_database()
        self._write_artifact("artifacts/call.bin")
        self._write_artifact("artifacts/rag.md")
        self.insert_call(
            "call-needs-rag",
            "owner-a",
            None,
            None,
            None,
            "artifacts/call.bin",
            rag_path="artifacts/rag.md",
        )
        registry = self.write_json(
            "owners.sanitized.json",
            {
                "active": ["owner-a"],
                "retired": [],
                "legacy_approved": [],
                "internal": [],
            },
        )
        _, report = self.invoke(owner_registry=registry)
        self.assertIn("missing_rag_metadata", self.finding_codes(report))

    def test_concurrent_artifact_metadata_change_is_detected(self) -> None:
        self.prepare_database()
        self._write_artifact("artifacts/call.bin", b"before")
        self.insert_call(
            "call-changing", "owner-a", None, None, None, "artifacts/call.bin"
        )
        registry = self.write_json(
            "owners.sanitized.json",
            {
                "active": ["owner-a"],
                "retired": [],
                "legacy_approved": [],
                "internal": [],
            },
        )
        original_artifact_metadata = inspector._artifact_metadata

        def mutate_after_observation(*args, **kwargs):
            result = original_artifact_metadata(*args, **kwargs)
            if args[4] == "stored_audio_path":
                (self.artifacts / "call.bin").write_bytes(b"after-change")
            return result

        with mock.patch.object(
            inspector,
            "_artifact_metadata",
            side_effect=mutate_after_observation,
        ):
            _, report = self.invoke(owner_registry=registry)
        self.assertIn(
            "concurrent_artifact_metadata_change", self.finding_codes(report)
        )

    def test_outside_root_missing_artifact_and_orphaned_metadata(self) -> None:
        self.prepare_database()
        self.insert_call(
            "call-missing",
            "owner-a",
            "upload-missing",
            "document-missing",
            "summary-missing",
            "artifacts/missing.bin",
            transcript_path="../outside.bin",
        )
        registry = self.write_json(
            "owners.sanitized.json",
            {
                "active": ["owner-a"],
                "retired": [],
                "legacy_approved": [],
                "internal": [],
            },
        )
        uploads = self.write_json(
            "uploads.sanitized.json",
            [
                {
                    "upload_id": "orphan-upload",
                    "owner": "owner-a",
                    "approved_relative_path_token": "artifacts/missing.bin",
                }
            ],
        )
        rag = self.write_json(
            "rag.sanitized.json",
            [
                {
                    "owner": "owner-a",
                    "source_path_token": "artifacts/missing-rag.md",
                    "call_or_video_id": "missing-workflow",
                    "document_ids": [],
                    "upload_id": "orphan-upload",
                    "chunk_id": "orphan-chunk",
                    "collection_or_lane_identifier": "marketmatch-calls",
                }
            ],
        )
        _, report = self.invoke(
            owner_registry=registry,
            upload_manifest=uploads,
            rag_manifest=rag,
        )
        codes = self.finding_codes(report)
        self.assertIn("artifact_path_outside_root", codes)
        self.assertIn("missing_or_unsafe_artifact", codes)
        self.assertIn("missing_upload_reference", codes)
        self.assertIn("orphaned_document_reference", codes)
        self.assertIn("orphaned_rag_workflow_reference", codes)
        self.assertIn("orphaned_upload_metadata", codes)

    def test_before_after_database_and_directory_equality(self) -> None:
        self.prepare_database()
        before = inspector.database_snapshot(self.database)
        status, report = self.invoke()
        after = inspector.database_snapshot(self.database)
        self.assertEqual(status, 0)
        self.assertEqual(
            inspector.mutation_relevant_snapshot(before),
            inspector.mutation_relevant_snapshot(after),
        )
        info_codes = [item["code"] for item in report["information"]]
        self.assertIn("non_mutation_verified", info_codes)
        proof = next(
            item for item in report["information"] if item["code"] == "non_mutation_verified"
        )
        self.assertFalse(proof["details"]["access_time_compared"])

    def test_real_database_use_flag_is_mandatory(self) -> None:
        self.prepare_database()
        status, report = inspector.run(
            [
                "--offline-root",
                os.fspath(self.root),
                "--database",
                "fixture.db",
                "--expected-sha256",
                file_hash(self.database),
            ]
        )
        self.assertNotEqual(status, 0)
        self.assertIn("real_database_use_blocked", self.finding_codes(report))


if __name__ == "__main__":
    unittest.main()
