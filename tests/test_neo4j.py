"""Opt-in smoke checks for the configured Neo4j research database."""

import os
from datetime import UTC, datetime

import pytest

from jaws import jaws_capture, jaws_finder
from jaws.config import DATABASE, NEO4J_PASSWORD, get_neo4j_driver
from jaws.domain import (
    CaptureId,
    CaptureRecord,
    CaptureSourceKind,
    CaptureState,
    EntityId,
)
from jaws.storage.migrations import MIGRATIONS, manager

pytestmark = pytest.mark.neo4j


def test_neo4j_connectivity():
    """A configured Neo4j tier must reach the declared database and execute a query."""
    if not NEO4J_PASSWORD:
        pytest.skip("NEO4J_PASSWORD is not set; Neo4j integration tier is unavailable")

    driver = get_neo4j_driver()
    driver.verify_connectivity()
    with driver.session(database=DATABASE) as session:
        assert session.run("RETURN 1 AS value").single()["value"] == 1


def _migration_test_database() -> str:
    database = os.environ.get("JAWS_NEO4J_MIGRATION_TEST_DATABASE")
    if not database:
        pytest.skip(
            "JAWS_NEO4J_MIGRATION_TEST_DATABASE is not set; refusing schema writes to an "
            "implicitly selected research database"
        )
    return database


def _reset_migration_fixture(driver, database):
    with driver.session(database=database) as session:
        session.run("MATCH (n:JAWS_MIGRATION_TEST_FIXTURE) DETACH DELETE n").consume()
        session.run(
            "MATCH (capture:CAPTURE) "
            "WHERE capture.CAPTURE_ID STARTS WITH 'cap_migration_fixture' "
            "DETACH DELETE capture"
        ).consume()
        session.run(
            "MATCH (scope:OBSERVATION_SCOPE) "
            "WHERE scope.MIGRATED_FROM_SCHEMA_VERSION = 2 "
            "   OR scope.SCOPE_ID STARTS WITH 'scope_cap_migration_fixture' "
            "DETACH DELETE scope"
        ).consume()
        session.run("MATCH (n:JAWS_SCHEMA_MIGRATION) DETACH DELETE n").consume()
        for migration in reversed(MIGRATIONS):
            for item in reversed(migration.required_schema):
                operation = "CONSTRAINT" if item.kind == "constraint" else "INDEX"
                session.run(f"DROP {operation} {item.name} IF EXISTS").consume()


@pytest.fixture
def disposable_migration_database():
    if not NEO4J_PASSWORD:
        pytest.skip("NEO4J_PASSWORD is not set; Neo4j integration tier is unavailable")
    database = _migration_test_database()
    driver = get_neo4j_driver()
    _reset_migration_fixture(driver, database)
    try:
        yield driver, database
    finally:
        _reset_migration_fixture(driver, database)


def test_fresh_database_reaches_managed_schema(disposable_migration_database):
    driver, database = disposable_migration_database

    result = manager(driver, database).migrate()

    assert result.applied_versions == (1, 2)
    assert result.status.is_current
    assert manager(driver, database).migrate().applied_versions == ()


def test_starting_revision_schema_is_adopted_without_losing_evidence(
    disposable_migration_database,
):
    driver, database = disposable_migration_database
    migration = MIGRATIONS[0]
    with driver.session(database=database) as session:
        for statement in migration.statements:
            if "jaws_schema_migration_version_unique" not in statement.query:
                session.run(statement.query).consume()
        session.run(
            "CREATE (:JAWS_MIGRATION_TEST_FIXTURE:CAPTURE {"
            "CAPTURE_ID: $capture_id, PACKETS: 3, SOURCE: 'legacy.pcap', "
            "STARTED: datetime('2026-08-10T15:30:00Z')}) "
            "CREATE (:JAWS_MIGRATION_TEST_FIXTURE:ENDPOINT {"
            "IP_ADDRESS: '8.8.8.8', CAPTURE_ID: $capture_id})",
            {"capture_id": "legacy-migration-fixture"},
        ).consume()

    result = manager(driver, database).migrate()

    assert result.status.is_current
    with driver.session(database=database) as session:
        record = session.run(
            "MATCH (scope:OBSERVATION_SCOPE)-[:INCLUDES]->(capture:CAPTURE {"
            "CAPTURE_ID: 'legacy-migration-fixture'}) "
            "MATCH (endpoint:ENDPOINT {CAPTURE_ID: 'legacy-migration-fixture'}) "
            "RETURN capture.CAPTURE_ID AS capture_id, "
            "       capture.LEGACY_CAPTURE_ID AS legacy_capture_id, "
            "       capture.STATE AS state, capture.SOURCE_KIND AS source_kind, "
            "       capture.PACKET_COUNT AS packet_count, scope.SCOPE_ID AS scope_id, "
            "       endpoint.SCOPE_ID AS endpoint_scope_id"
        ).single()
        assert record["capture_id"] == "legacy-migration-fixture"
        assert record["legacy_capture_id"] == "legacy-migration-fixture"
        assert record["state"] == "complete"
        assert record["source_kind"] == "legacy_unknown"
        assert record["packet_count"] == 3
        assert record["scope_id"] == "scope_legacy-migration-fixture"
        assert record["endpoint_scope_id"] == record["scope_id"]


def test_collision_resistant_capture_identity_rejects_duplicate_starts(
    disposable_migration_database,
):
    driver, database = disposable_migration_database
    manager(driver, database).migrate()
    registered_at = datetime(2026, 8, 10, 15, 30, tzinfo=UTC)
    record = CaptureRecord(
        capture_id=CaptureId("cap_migration_fixture_duplicate"),
        source_kind=CaptureSourceKind.LIVE_INTERFACE,
        source_name="eth0",
        state=CaptureState.REGISTERED,
        registered_at=registered_at,
        legacy_capture_id="20260810T153000Z",
        perspective=EntityId("ip:10.0.0.2"),
        tool_versions={"jaws": "2.0.0"},
    ).transition(CaptureState.RUNNING, registered_at)

    jaws_capture.register_capture(driver, database, record)
    with pytest.raises(Exception) as error:
        jaws_capture.register_capture(driver, database, record)
    assert "Constraint" in type(error.value).__name__


def test_historical_profile_query_orders_opaque_ids_by_capture_time(
    disposable_migration_database,
):
    driver, database = disposable_migration_database
    manager(driver, database).migrate()
    historical_id = "cap_migration_fixture_z_history"
    target_id = "cap_migration_fixture_a_target"
    with driver.session(database=database) as session:
        session.run(
            "CREATE (:CAPTURE {CAPTURE_ID: $historical_id, "
            "STARTED_AT: datetime('2026-08-10T15:30:00Z')}) "
            "CREATE (:CAPTURE {CAPTURE_ID: $target_id, "
            "STARTED_AT: datetime('2026-08-10T15:31:00Z')}) "
            "CREATE (:JAWS_MIGRATION_TEST_FIXTURE:ENDPOINT {"
            "IP_ADDRESS: '8.8.8.8', CAPTURE_ID: $historical_id})",
            {"historical_id": historical_id, "target_id": target_id},
        ).consume()
        rows = list(
            session.run(
                jaws_finder._HISTORY_QUERY,
                {"scope": target_id, "pooled": "all"},
            )
        )

    assert [record["capture_id"] for record in rows] == [historical_id]
