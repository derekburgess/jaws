"""Deterministic contract tests for schema migration planning and recovery."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from jaws.domain import CanonicalDigest
from jaws.ports import FrozenClock
from jaws.storage.migrations import (
    MIGRATIONS,
    Migration,
    MigrationError,
    MigrationSafety,
    Neo4jMigrationManager,
    VerifiedMigrationBackup,
)
from jaws.storage.migrations.models import SchemaObject


@dataclass
class FakeResult:
    records: list[dict[str, object]] = field(default_factory=list)

    def __iter__(self) -> Iterator[dict[str, object]]:
        return iter(self.records)

    def consume(self) -> None:
        return None


@dataclass
class FakeNeo4j:
    constraints: dict[str, SchemaObject] = field(default_factory=dict)
    indexes: dict[str, SchemaObject] = field(default_factory=dict)
    applied: list[dict[str, object]] = field(default_factory=list)
    writes: list[str] = field(default_factory=list)
    fail_on: str | None = None
    databases: list[str] = field(default_factory=list)

    def session(self, *, database: str) -> FakeNeo4j:
        self.databases.append(database)
        return self

    def __enter__(self) -> FakeNeo4j:
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        return None

    def execute_write(self, work: Callable[[FakeNeo4j], Any]) -> Any:
        return work(self)

    def run(self, query: str, parameters: Mapping[str, object] | None = None) -> FakeResult:
        normalized = " ".join(query.split())
        if self.fail_on and self.fail_on in normalized:
            raise RuntimeError(f"fixture failure: {self.fail_on}")
        if normalized.startswith("MATCH (migration:JAWS_SCHEMA_MIGRATION)"):
            return FakeResult(list(self.applied))
        if normalized.startswith("SHOW CONSTRAINTS"):
            return FakeResult([self._schema_record(item) for item in self.constraints.values()])
        if normalized.startswith("SHOW INDEXES"):
            return FakeResult([self._schema_record(item) for item in self.indexes.values()])
        if normalized.startswith("CREATE CONSTRAINT"):
            self.writes.append(normalized)
            self._create_schema("constraint", normalized)
            return FakeResult()
        if normalized.startswith("CREATE INDEX"):
            self.writes.append(normalized)
            self._create_schema("index", normalized)
            return FakeResult()
        if normalized.startswith("CALL db.awaitIndexes"):
            return FakeResult()
        if normalized.startswith("CREATE (:JAWS_SCHEMA_MIGRATION"):
            assert parameters is not None
            self.writes.append(normalized)
            self.applied.append(
                {
                    "version": parameters["version"],
                    "name": parameters["name"],
                    "checksum": parameters["checksum"],
                    "applied_at": parameters["applied_at"],
                }
            )
            return FakeResult()
        if (
            normalized.startswith("MATCH (capture:CAPTURE)")
            or normalized.startswith("MATCH (endpoint:ENDPOINT")
            or normalized.startswith("MERGE (scope:OBSERVATION_SCOPE")
        ):
            self.writes.append(normalized)
            return FakeResult()
        raise AssertionError(f"unexpected query: {normalized}")

    @staticmethod
    def _schema_record(item: SchemaObject) -> dict[str, object]:
        return {
            "name": item.name,
            "labelsOrTypes": [item.label],
            "properties": list(item.properties),
        }

    def _create_schema(self, kind: str, query: str) -> None:
        name = re.search(r"CREATE (?:CONSTRAINT|INDEX) (\w+)", query)
        assert name is not None
        expected = next(
            item
            for migration in MIGRATIONS
            for item in migration.required_schema
            if item.kind == kind and item.name == name.group(1)
        )
        target = self.constraints if kind == "constraint" else self.indexes
        target[expected.name] = expected


@pytest.fixture
def clock() -> FrozenClock:
    return FrozenClock(datetime(2026, 8, 10, 15, 30, tzinfo=UTC))


def _manager(graph: FakeNeo4j, clock: FrozenClock) -> Neo4jMigrationManager:
    return Neo4jMigrationManager(graph, "captures", MIGRATIONS, clock=clock)


def test_version_one_matches_the_frozen_legacy_schema_contract():
    migration = MIGRATIONS[0]
    assert migration.version == 1
    assert len(migration.checksum) == 64
    assert migration.reversible is False
    assert {item.name for item in migration.required_schema if item.kind == "constraint"} == {
        "capture_id_unique",
        "ip_address_unique",
        "jaws_schema_migration_version_unique",
        "organization_unique",
    }
    assert {item.name for item in migration.required_schema if item.kind == "index"} == {
        "endpoint_capture_index",
        "endpoint_ip_index",
        "packet_capture_index",
        "packet_timestamp_index",
        "port_composite_index",
    }


def test_version_two_adds_identity_lifecycle_scope_and_profile_contracts():
    migration = MIGRATIONS[1]
    assert migration.version == 2
    assert len(migration.checksum) == 64
    assert migration.reversible is True
    assert {item.name for item in migration.required_schema if item.kind == "constraint"} == {
        "endpoint_profile_key_unique",
        "observation_scope_id_unique",
    }
    assert {item.name for item in migration.required_schema if item.kind == "index"} == {
        "capture_legacy_id_index",
        "capture_state_index",
        "endpoint_scope_index",
    }


def test_version_three_adds_enrichment_provenance_and_profile_scope_semantics():
    migration = MIGRATIONS[2]
    assert migration.version == 3
    assert len(migration.checksum) == 64
    assert migration.reversible is True
    assert {item.name for item in migration.required_schema if item.kind == "constraint"} == {
        "entity_annotation_key_unique"
    }
    assert {item.name for item in migration.required_schema if item.kind == "index"} == {
        "endpoint_scope_computed_index",
        "ip_enrichment_status_index",
    }
    source = " ".join(" ".join(statement.query.split()) for statement in migration.statements)
    assert "scope_pooled_all" in source
    assert "scope_legacy_unstamped" in source
    assert "legacy_quarantined" in source
    assert "not_scored" in source
    assert "DETACH DELETE endpoint" not in source


def test_version_four_adds_payload_free_administration_audit_indexes():
    migration = MIGRATIONS[3]
    assert migration.version == 4
    assert len(migration.checksum) == 64
    assert migration.reversible is True
    assert {item.name for item in migration.required_schema if item.kind == "constraint"} == {
        "jaws_audit_event_id_unique"
    }
    assert {item.name for item in migration.required_schema if item.kind == "index"} == {
        "jaws_audit_occurred_at_index",
        "jaws_audit_operation_target_index",
    }


def test_version_five_adds_only_experiment_run_discovery_indexes():
    migration = MIGRATIONS[4]
    assert migration.version == 5
    assert migration.reversible is True
    assert migration.backup_required is False
    assert {item.name for item in migration.required_schema if item.kind == "constraint"} == {
        "jaws_experiment_id_unique",
        "jaws_experiment_run_id_unique",
    }
    assert {item.name for item in migration.required_schema if item.kind == "index"} == {
        "jaws_experiment_run_artifact_uri_index",
        "jaws_experiment_run_state_created_index",
    }
    assert all("DELETE" not in statement.query for statement in migration.statements)


def test_version_six_adds_only_reconstructable_finding_discovery_indexes():
    migration = MIGRATIONS[5]
    assert migration.version == 6
    assert migration.reversible is True
    assert migration.backup_required is False
    assert {item.name for item in migration.required_schema if item.kind == "constraint"} == {
        "jaws_finding_index_id_unique"
    }
    assert {item.name for item in migration.required_schema if item.kind == "index"} == {
        "jaws_finding_entity_index",
        "jaws_finding_run_rank_index",
    }
    assert all("DELETE" not in statement.query for statement in migration.statements)


def test_version_seven_adds_only_descriptive_capture_source_index():
    migration = MIGRATIONS[6]
    assert migration.version == 7
    assert migration.reversible is True
    assert migration.backup_required is False
    assert migration.required_schema == (
        SchemaObject(
            "index",
            "capture_source_file_name_index",
            "CAPTURE",
            ("SOURCE_FILE_NAME",),
        ),
    )
    assert all("DELETE" not in statement.query for statement in migration.statements)


def test_existing_additive_migration_checksums_remain_immutable():
    assert tuple(migration.checksum for migration in MIGRATIONS) == (
        "22e1720bf04aa692ab3bc71a9e23a880829923f11c44bf38b164e21c1e9bb081",
        "15fcd747c31d83a845e32d2fbe0141dd44474e7fc57eec7b55b577cd9bb50142",
        "3cdd6d0cc56136206bb3c3d0cc215c3f0cfbf967830c1627ddc255787744cfa3",
        "8cf6114bb284d64eac5715c59e87720b302790c9b707e722fe565f14053ae37a",
        "e2510fbe1c78bb6a468c85e4d9ebb1892e5eee6609480ad87b27a944f20c5487",
        "367c2bc68824f8faeb99751b8686aa0296727ca8406d645dffbf4aa0bb6205a9",
        "cf7ca753da2a5e41a396bac3bccbd94e10264f0c3e96cc8cf39e95f973608f24",
    )
    assert all(migration.safety is MigrationSafety.ADDITIVE for migration in MIGRATIONS)
    assert not MIGRATIONS[0].backup_required


def test_legacy_utility_delegates_schema_ownership_to_migrations():
    utility_source = Path("jaws/jaws_utils.py").read_text(encoding="utf-8")
    migration_source = Path("jaws/storage/migrations/v0001_adopt_legacy_schema.py").read_text(
        encoding="utf-8"
    )
    assert "CREATE CONSTRAINT" not in utility_source
    assert "CREATE INDEX" not in utility_source
    assert "CREATE CONSTRAINT" in migration_source
    assert "CREATE INDEX" in migration_source


def test_fresh_database_dry_run_is_read_only_and_migration_is_idempotent(clock):
    graph = FakeNeo4j()
    migration_manager = _manager(graph, clock)

    status = migration_manager.status()
    assert status.current_version is None
    assert status.pending_versions == (1, 2, 3, 4, 5, 6, 7)
    plan = migration_manager.dry_run()
    assert plan.current_version is None
    assert plan.target_version == 7
    assert plan.pending == MIGRATIONS
    assert len(plan.statements) == sum(len(migration.statements) for migration in MIGRATIONS)
    assert graph.writes == []

    result = migration_manager.migrate()
    assert result.applied_versions == (1, 2, 3, 4, 5, 6, 7)
    assert result.status.is_current
    assert graph.applied == [
        {
            "version": migration.version,
            "name": migration.name,
            "checksum": migration.checksum,
            "applied_at": "2026-08-10T15:30:00.000000Z",
        }
        for migration in MIGRATIONS
    ]

    writes = list(graph.writes)
    repeated = migration_manager.migrate()
    assert repeated.applied_versions == ()
    assert graph.writes == writes


def test_legacy_schema_is_adopted_without_requiring_a_data_rewrite(clock):
    legacy = tuple(
        item
        for item in MIGRATIONS[0].required_schema
        if item.name != "jaws_schema_migration_version_unique"
    )
    graph = FakeNeo4j(
        constraints={item.name: item for item in legacy if item.kind == "constraint"},
        indexes={item.name: item for item in legacy if item.kind == "index"},
    )

    result = _manager(graph, clock).migrate()

    assert result.status.is_current
    expected_constraints = {
        item.name
        for migration in MIGRATIONS
        for item in migration.required_schema
        if item.kind == "constraint"
    }
    expected_indexes = {
        item.name
        for migration in MIGRATIONS
        for item in migration.required_schema
        if item.kind == "index"
    }
    assert set(graph.constraints) == expected_constraints
    assert set(graph.indexes) == expected_indexes


def test_partial_schema_application_writes_no_version_and_can_retry(clock):
    graph = FakeNeo4j(fail_on="packet_capture_index")
    migration_manager = _manager(graph, clock)

    with pytest.raises(RuntimeError, match="fixture failure"):
        migration_manager.migrate()
    assert graph.applied == []
    assert graph.constraints
    assert graph.indexes

    graph.fail_on = None
    result = migration_manager.migrate()
    assert result.status.is_current
    assert len(graph.applied) == 7


def test_applied_checksum_drift_blocks_validation_and_migration(clock):
    graph = FakeNeo4j()
    migration_manager = _manager(graph, clock)
    migration_manager.migrate()
    graph.applied[0]["checksum"] = "0" * 64

    status = migration_manager.validate()
    assert not status.is_current
    assert "migration 1 checksum does not match registry" in status.issues
    with pytest.raises(MigrationError, match="checksum does not match"):
        migration_manager.migrate()


def test_missing_required_object_is_reported_after_application(clock):
    graph = FakeNeo4j()
    migration_manager = _manager(graph, clock)
    migration_manager.migrate()
    del graph.indexes["packet_timestamp_index"]

    status = migration_manager.validate()
    assert not status.is_current
    assert "applied migration 1 is missing index packet_timestamp_index" in status.issues


def test_noncontiguous_applied_history_blocks_an_earlier_migration(clock):
    second = Migration(
        version=2,
        name="fixture_second",
        statements=MIGRATIONS[0].statements,
        required_schema=(),
        reversible=False,
        rollback="fixture",
    )
    graph = FakeNeo4j(
        applied=[
            {
                "version": 2,
                "name": second.name,
                "checksum": second.checksum,
                "applied_at": "2026-08-10T15:30:00.000000Z",
            }
        ]
    )
    migration_manager = Neo4jMigrationManager(
        graph, "captures", (MIGRATIONS[0], second), clock=clock
    )

    status = migration_manager.validate()
    assert "applied migration history is not contiguous from version 1" in status.issues
    with pytest.raises(MigrationError, match="not contiguous"):
        migration_manager.dry_run()


def _risky_manager(graph: FakeNeo4j, clock: FrozenClock) -> Neo4jMigrationManager:
    risky = Migration(
        version=8,
        name="fixture_destructive_rewrite",
        statements=(MIGRATIONS[1].statements[5],),
        required_schema=(),
        reversible=False,
        rollback="restore the verified evidence bundle",
        safety=MigrationSafety.DESTRUCTIVE,
    )
    return Neo4jMigrationManager(graph, "captures", (*MIGRATIONS, risky), clock=clock)


def _backup(plan) -> VerifiedMigrationBackup:
    assert plan.source_schema_digest is not None
    return VerifiedMigrationBackup(
        target_database="captures",
        target_version=plan.target_version,
        protected_versions=plan.backup_required_versions,
        source_schema_digest=plan.source_schema_digest,
        evidence_content_checksum=CanonicalDigest("e" * 64),
        exported_at=datetime(2026, 8, 13, 15, 30, tzinfo=UTC),
        bundle_path="/secure/evidence.json",
    )


def test_risky_migration_plan_names_backup_requirement_and_fails_closed(clock):
    graph = FakeNeo4j()
    _manager(graph, clock).migrate()
    migration_manager = _risky_manager(graph, clock)

    plan = migration_manager.dry_run()

    assert plan.backup_required
    assert plan.backup_required_versions == (8,)
    assert plan.source_schema_digest is not None
    writes = list(graph.writes)
    with pytest.raises(MigrationError, match="verified evidence backup required"):
        migration_manager.migrate()
    assert graph.writes == writes
    assert len(graph.applied) == 7


def test_risky_migration_rejects_mismatched_proof_and_accepts_exact_proof(clock):
    graph = FakeNeo4j()
    _manager(graph, clock).migrate()
    migration_manager = _risky_manager(graph, clock)
    plan = migration_manager.dry_run()
    proof = _backup(plan)
    wrong_database = VerifiedMigrationBackup(
        target_database="other",
        target_version=proof.target_version,
        protected_versions=proof.protected_versions,
        source_schema_digest=proof.source_schema_digest,
        evidence_content_checksum=proof.evidence_content_checksum,
        exported_at=proof.exported_at,
        bundle_path=proof.bundle_path,
    )

    with pytest.raises(MigrationError, match="different database"):
        migration_manager.migrate(wrong_database)
    assert len(graph.applied) == 7

    result = migration_manager.migrate(proof)
    assert result.applied_versions == (8,)
    assert result.status.is_current


@pytest.mark.parametrize("versions", [(2,), (1, 3), (1, 1)])
def test_registry_versions_must_be_contiguous_and_unique(versions):
    migrations = tuple(
        MIGRATIONS[0].__class__(
            version=version,
            name=f"fixture_{position}",
            statements=MIGRATIONS[0].statements,
            required_schema=MIGRATIONS[0].required_schema,
            reversible=False,
            rollback="fixture",
        )
        for position, version in enumerate(versions)
    )
    with pytest.raises(ValueError, match="contiguous"):
        Neo4jMigrationManager(FakeNeo4j(), "captures", migrations)
