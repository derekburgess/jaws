"""Opt-in smoke checks for the configured Neo4j research database."""

import os

import pytest

from jaws.config import DATABASE, NEO4J_PASSWORD, get_neo4j_driver
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
    migration = MIGRATIONS[0]
    with driver.session(database=database) as session:
        session.run("MATCH (n:JAWS_MIGRATION_TEST_FIXTURE) DETACH DELETE n").consume()
        session.run("MATCH (n:JAWS_SCHEMA_MIGRATION) DETACH DELETE n").consume()
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

    assert result.applied_versions == (1,)
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
            "CREATE (:JAWS_MIGRATION_TEST_FIXTURE:CAPTURE {CAPTURE_ID: $capture_id})",
            {"capture_id": "legacy-migration-fixture"},
        ).consume()

    result = manager(driver, database).migrate()

    assert result.status.is_current
    with driver.session(database=database) as session:
        record = session.run(
            "MATCH (n:JAWS_MIGRATION_TEST_FIXTURE) "
            "RETURN n.CAPTURE_ID AS capture_id, count(n) AS count"
        ).single()
        assert record["capture_id"] == "legacy-migration-fixture"
        assert record["count"] == 1
