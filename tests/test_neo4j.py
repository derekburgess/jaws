"""Opt-in smoke checks for the configured Neo4j research database."""

import os
from datetime import UTC, datetime

import pytest
from evidence_contract import OBSERVED_AT, evidence_fixture
from experiment_index_repository_contract import assert_experiment_index_repository_contract
from inspection_repository_contract import assert_inspection_repository_contract
from profile_repository_contract import assert_enrichment_and_profile_repository_contract
from repository_contract import assert_capture_and_packet_repository_contract
from retention_contract import assert_retention_service_contract

from jaws.adapters import write_evidence_bundle
from jaws.adapters.migration_backup import verify_migration_backup
from jaws.config import DATABASE, NEO4J_PASSWORD, get_neo4j_driver
from jaws.domain import (
    AuditContext,
    AuditEventId,
    CaptureId,
    CaptureRecord,
    CaptureSourceKind,
    CaptureState,
    EntityId,
    ObservationScopeId,
)
from jaws.ports import (
    AdministrationConflictError,
    DuplicateCaptureError,
    FrozenClock,
    SequenceIdGenerator,
)
from jaws.services import AdministrationService, EvidenceTransferService
from jaws.storage import Neo4jDatabaseRuntime, Neo4jRepositories
from jaws.storage.migrations import (
    MIGRATIONS,
    Migration,
    MigrationError,
    MigrationSafety,
    Neo4jMigrationManager,
    manager,
)

pytestmark = pytest.mark.neo4j


def test_neo4j_connectivity():
    """A configured Neo4j tier must reach the declared database and execute a query."""
    if not NEO4J_PASSWORD:
        pytest.skip("NEO4J_PASSWORD is not set; Neo4j integration tier is unavailable")

    driver = get_neo4j_driver()
    driver.verify_connectivity()
    database = os.environ.get("JAWS_NEO4J_MIGRATION_TEST_DATABASE", DATABASE)
    with driver.session(database=database) as session:
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
        # This fixture runs only against the explicitly named disposable migration-test
        # database. Clear every data node so strict empty-target contracts cannot inherit
        # orphaned derived nodes from an earlier repository contract.
        session.run("MATCH (node) DETACH DELETE node").consume()
        session.run("MATCH (n:JAWS_MIGRATION_TEST_FIXTURE) DETACH DELETE n").consume()
        session.run(
            "MATCH (capture:CAPTURE) "
            "WHERE capture.CAPTURE_ID STARTS WITH 'cap_migration_fixture' "
            "   OR capture.CAPTURE_ID STARTS WITH 'cap_repository_fixture' "
            "   OR capture.CAPTURE_ID STARTS WITH 'cap_profile_fixture' "
            "   OR capture.CAPTURE_ID STARTS WITH 'cap_inspection_fixture' "
            "   OR capture.CAPTURE_ID STARTS WITH 'cap_retention_fixture' "
            "   OR capture.CAPTURE_ID STARTS WITH 'cap_evidence_fixture' "
            "DETACH DELETE capture"
        ).consume()
        session.run(
            "MATCH (packet:PACKET) "
            "WHERE packet.CAPTURE_ID STARTS WITH 'cap_repository_fixture' "
            "   OR packet.CAPTURE_ID STARTS WITH 'cap_inspection_fixture' "
            "   OR packet.CAPTURE_ID STARTS WITH 'cap_retention_fixture' "
            "   OR packet.CAPTURE_ID STARTS WITH 'cap_evidence_fixture' "
            "DETACH DELETE packet"
        ).consume()
        session.run(
            "MATCH (endpoint:ENDPOINT) "
            "WHERE endpoint.CAPTURE_ID STARTS WITH 'cap_profile_fixture' "
            "   OR endpoint.CAPTURE_ID STARTS WITH 'cap_inspection_fixture' "
            "   OR endpoint.CAPTURE_ID STARTS WITH 'cap_retention_fixture' "
            "   OR endpoint.CAPTURE_ID STARTS WITH 'cap_evidence_fixture' "
            "   OR endpoint.SCOPE_ID IN ["
            "'scope_cap_evidence_fixture', 'scope_legacy_unstamped'] "
            "DETACH DELETE endpoint"
        ).consume()
        session.run(
            "MATCH (scope:OBSERVATION_SCOPE) "
            "WHERE scope.MIGRATED_FROM_SCHEMA_VERSION = 2 "
            "   OR scope.MIGRATED_FROM_SCHEMA_VERSION = 3 "
            "   OR scope.SCOPE_ID STARTS WITH 'scope_cap_migration_fixture' "
            "   OR scope.SCOPE_ID STARTS WITH 'scope_cap_repository_fixture' "
            "   OR scope.SCOPE_ID STARTS WITH 'scope_profile_fixture' "
            "   OR scope.SCOPE_ID STARTS WITH 'scope_cap_inspection_fixture' "
            "   OR scope.SCOPE_ID STARTS WITH 'scope_cap_retention_fixture' "
            "   OR scope.SCOPE_ID STARTS WITH 'scope_cap_evidence_fixture' "
            "   OR scope.SCOPE_ID IN ['scope_pooled_all', 'scope_legacy_unstamped'] "
            "DETACH DELETE scope"
        ).consume()
        session.run("MATCH (annotation:ENTITY_ANNOTATION) DETACH DELETE annotation").consume()
        session.run(
            "MATCH (organization:ORGANIZATION) "
            "WHERE organization.ORGANIZATION IN ["
            "'Evidence Fixture Networks', 'Legacy Fixture Networks'] "
            "DETACH DELETE organization"
        ).consume()
        session.run(
            "MATCH (port:PORT) "
            "WHERE port.IP_ADDRESS IN ["
            "'192.0.2.10', '198.51.100.20', '203.0.113.30'] "
            "DETACH DELETE port"
        ).consume()
        session.run(
            "MATCH (address:IP_ADDRESS) "
            "WHERE address.IP_ADDRESS IN ["
            "'192.0.2.10', '198.51.100.20', '203.0.113.30'] "
            "DETACH DELETE address"
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

    assert result.applied_versions == (1, 2, 3, 4, 5)
    assert result.status.is_current
    assert manager(driver, database).migrate().applied_versions == ()

    runtime = Neo4jDatabaseRuntime(driver, database)
    runtime.probe()
    runtime.ensure_local_perspective("192.0.2.10")
    runtime.ensure_local_perspective("192.0.2.10")
    with driver.session(database=database) as session:
        seed = session.run(
            "MATCH (organization:ORGANIZATION {ORGANIZATION: 'YOU ARE HERE'})"
            "-[ownership:OWNERSHIP]->"
            "(address:IP_ADDRESS {IP_ADDRESS: '192.0.2.10'}) "
            "RETURN count(organization) AS organizations, "
            "count(ownership) AS ownerships, count(address) AS addresses"
        ).single()
    assert seed.data() == {"organizations": 1, "ownerships": 1, "addresses": 1}


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
            "IP_ADDRESS: '8.8.8.8', CAPTURE_ID: $capture_id, OUTLIER: false}) "
            "CREATE (:JAWS_MIGRATION_TEST_FIXTURE:ENDPOINT {"
            "IP_ADDRESS: '1.1.1.1', CAPTURE_ID: 'all', OUTLIER: true}) "
            "CREATE (:JAWS_MIGRATION_TEST_FIXTURE:ENDPOINT {"
            "IP_ADDRESS: '192.0.2.1'})",
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
        legacy_profiles = {
            row["ip_address"]: row.data()
            for row in session.run(
                "MATCH (endpoint:JAWS_MIGRATION_TEST_FIXTURE:ENDPOINT) "
                "RETURN endpoint.IP_ADDRESS AS ip_address, "
                "endpoint.SCOPE_ID AS scope_id, "
                "endpoint.PROFILE_STATUS AS profile_status, "
                "endpoint.OUTLIER_STATUS AS outlier_status, "
                "endpoint.OUTLIER AS legacy_outlier"
            )
        }
        assert legacy_profiles["8.8.8.8"]["profile_status"] == "legacy_unversioned"
        assert legacy_profiles["8.8.8.8"]["outlier_status"] == "inlier"
        assert legacy_profiles["8.8.8.8"]["legacy_outlier"] is False
        assert legacy_profiles["1.1.1.1"]["scope_id"] == "scope_pooled_all"
        assert legacy_profiles["1.1.1.1"]["outlier_status"] == "outlier"
        assert legacy_profiles["192.0.2.1"]["scope_id"] == "scope_legacy_unstamped"
        assert legacy_profiles["192.0.2.1"]["profile_status"] == "legacy_quarantined"
        assert legacy_profiles["192.0.2.1"]["outlier_status"] == "not_scored"


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

    repositories = Neo4jRepositories.connect(driver, database)
    repositories.captures.add(record)
    with pytest.raises(DuplicateCaptureError) as error:
        repositories.captures.add(record)
    assert "already exists" in str(error.value)


def test_capture_and_packet_repositories_follow_shared_contract(
    disposable_migration_database,
):
    driver, database = disposable_migration_database
    manager(driver, database).migrate()

    repositories = Neo4jRepositories.connect(driver, database)

    assert_capture_and_packet_repository_contract(repositories)


def test_enrichment_and_profile_repositories_follow_shared_contract(
    disposable_migration_database,
):
    driver, database = disposable_migration_database
    manager(driver, database).migrate()
    with driver.session(database=database) as session:
        session.run(
            "MATCH (unknown:ORGANIZATION {ORGANIZATION: 'Unknown'}) DETACH DELETE unknown"
        ).consume()
        session.run(
            "CREATE (first:IP_ADDRESS {IP_ADDRESS: '192.0.2.10'}) "
            "CREATE (second:IP_ADDRESS {IP_ADDRESS: '198.51.100.20'}) "
            "CREATE (unknown:ORGANIZATION {ORGANIZATION: 'Unknown'}) "
            "CREATE (unknown)-[:OWNERSHIP]->(first) "
            "CREATE (first_capture:CAPTURE {"
            "CAPTURE_ID: 'cap_profile_fixture_first', "
            "STARTED_AT: datetime('2026-08-12T12:00:00Z')}) "
            "CREATE (second_capture:CAPTURE {"
            "CAPTURE_ID: 'cap_profile_fixture_second', "
            "STARTED_AT: datetime('2026-08-12T12:00:01Z')}) "
            "CREATE (first_scope:OBSERVATION_SCOPE {"
            "SCOPE_ID: 'scope_profile_fixture_first'})-[:INCLUDES]->(first_capture) "
            "CREATE (second_scope:OBSERVATION_SCOPE {"
            "SCOPE_ID: 'scope_profile_fixture_second'})-[:INCLUDES]->(second_capture)"
        ).consume()

    repositories = Neo4jRepositories.connect(driver, database)

    assert_enrichment_and_profile_repository_contract(repositories)


def test_inspection_repository_follows_shared_contract(disposable_migration_database):
    driver, database = disposable_migration_database
    manager(driver, database).migrate()

    repositories = Neo4jRepositories.connect(driver, database)

    assert_inspection_repository_contract(repositories)


def test_experiment_index_repository_follows_shared_contract(
    disposable_migration_database,
):
    driver, database = disposable_migration_database
    manager(driver, database).migrate()
    repositories = Neo4jRepositories.connect(driver, database)

    assert_experiment_index_repository_contract(repositories.experiments)


def test_retention_service_follows_shared_contract(disposable_migration_database):
    driver, database = disposable_migration_database
    manager(driver, database).migrate()

    repositories = Neo4jRepositories.connect(driver, database)

    assert_retention_service_contract(repositories)


def test_evidence_export_import_round_trip_preserves_snapshot(
    disposable_migration_database,
):
    driver, database = disposable_migration_database
    manager(driver, database).migrate()
    repositories = Neo4jRepositories.connect(driver, database)
    repositories.evidence.restore(evidence_fixture())
    service = EvidenceTransferService(repositories.evidence, FrozenClock(OBSERVED_AT))

    bundle = service.export()
    assert bundle.evidence == evidence_fixture()
    with driver.session(database=database) as session:
        session.run(
            "MATCH (node) WHERE NOT node:JAWS_SCHEMA_MIGRATION DETACH DELETE node"
        ).consume()

    plan = service.plan_import(bundle)
    assert plan.target_empty
    result = service.apply_import(bundle, plan)

    assert result.applied
    assert repositories.evidence.snapshot() == bundle.evidence
    assert repositories.evidence.snapshot().content_checksum == bundle.evidence.content_checksum


def test_guarded_administration_preserves_schema_and_audit_on_disposable_database(
    disposable_migration_database,
):
    driver, database = disposable_migration_database
    manager(driver, database).migrate()
    repositories = Neo4jRepositories.connect(driver, database)
    repositories.evidence.restore(evidence_fixture())
    service = AdministrationService(
        repositories.administration,
        FrozenClock(OBSERVED_AT),
        SequenceIdGenerator(
            (AuditEventId("audit_admin_stale"), AuditEventId("audit_admin_applied"))
        ),
        AuditContext("integration_test", "pytest-neo4j", database),
    )

    stale = service.plan()
    with driver.session(database=database) as session:
        session.run("CREATE (:JAWS_MIGRATION_TEST_FIXTURE {VALUE: 'changed'})").consume()
    with pytest.raises(AdministrationConflictError, match="changed"):
        service.erase(stale, stale.confirmation)

    plan = service.plan()
    result = service.erase(plan, plan.confirmation)

    assert result.deleted_nodes == plan.node_count
    assert result.deleted_relationships == plan.relationship_count
    assert repositories.evidence.is_empty()
    assert repositories.administration.plan().node_count == 0
    events = repositories.administration.audit_events()
    assert tuple(event.event_id.value for event in events) == ("audit_admin_applied",)
    with driver.session(database=database) as session:
        assert session.run("MATCH (m:JAWS_SCHEMA_MIGRATION) RETURN count(m) AS count").single()[
            "count"
        ] == len(MIGRATIONS)
        scopes = session.run(
            "MATCH (scope:OBSERVATION_SCOPE) RETURN collect(scope.SCOPE_ID) AS ids"
        ).single()["ids"]
        assert set(scopes) == {"scope_pooled_all", "scope_legacy_unstamped"}


def test_destructive_migration_requires_current_verified_evidence_backup(
    disposable_migration_database,
    tmp_path,
):
    driver, database = disposable_migration_database
    manager(driver, database).migrate()
    repositories = Neo4jRepositories.connect(driver, database)
    repositories.evidence.restore(evidence_fixture())
    bundle = EvidenceTransferService(repositories.evidence, FrozenClock(OBSERVED_AT)).export()
    backup_path = tmp_path / "pre-migration-evidence.json"
    write_evidence_bundle(backup_path, bundle)
    destructive = Migration(
        version=6,
        name="fixture_delete_profiles",
        statements=(
            MIGRATIONS[0]
            .statements[0]
            .__class__(
                "delete profiles in disposable fixture",
                "MATCH (endpoint:ENDPOINT) DETACH DELETE endpoint",
            ),
        ),
        required_schema=(),
        reversible=False,
        rollback="restore the verified evidence bundle",
        safety=MigrationSafety.DESTRUCTIVE,
    )
    migration_manager = Neo4jMigrationManager(
        driver,
        database,
        (*MIGRATIONS, destructive),
        clock=FrozenClock(OBSERVED_AT),
    )
    plan = migration_manager.dry_run()

    with pytest.raises(MigrationError, match="verified evidence backup required"):
        migration_manager.migrate()
    with driver.session(database=database) as session:
        assert session.run("MATCH (endpoint:ENDPOINT) RETURN count(endpoint) AS count").single()[
            "count"
        ] == len(bundle.evidence.profiles)

    with driver.session(database=database) as session:
        session.run(
            "CREATE (:JAWS_MIGRATION_TEST_FIXTURE:IP_ADDRESS {IP_ADDRESS: '203.0.113.99'})"
        ).consume()
    with pytest.raises(ValueError, match="stale or belongs to different"):
        verify_migration_backup(
            backup_path,
            database=database,
            plan=plan,
            evidence=repositories.evidence,
        )
    with driver.session(database=database) as session:
        session.run("MATCH (node:JAWS_MIGRATION_TEST_FIXTURE) DETACH DELETE node").consume()

    proof = verify_migration_backup(
        backup_path,
        database=database,
        plan=plan,
        evidence=repositories.evidence,
    )
    result = migration_manager.migrate(proof)

    assert result.applied_versions == (6,)
    with driver.session(database=database) as session:
        assert (
            session.run("MATCH (endpoint:ENDPOINT) RETURN count(endpoint) AS count").single()[
                "count"
            ]
            == 0
        )


def test_profile_history_repository_orders_opaque_ids_by_capture_time(
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
        session.run(
            "MATCH (historical_capture:CAPTURE {CAPTURE_ID: $historical_id}) "
            "MATCH (target_capture:CAPTURE {CAPTURE_ID: $target_id}) "
            "CREATE (historical_scope:OBSERVATION_SCOPE {"
            "SCOPE_ID: 'scope_' + $historical_id})-[:INCLUDES]->(historical_capture) "
            "CREATE (target_scope:OBSERVATION_SCOPE {"
            "SCOPE_ID: 'scope_' + $target_id})-[:INCLUDES]->(target_capture) "
            "WITH historical_scope "
            "MATCH (endpoint:ENDPOINT {CAPTURE_ID: $historical_id}) "
            "SET endpoint.SCOPE_ID = historical_scope.SCOPE_ID, "
            "endpoint.PROFILE_STATUS = 'legacy_unversioned'",
            {"historical_id": historical_id, "target_id": target_id},
        ).consume()

    rows = Neo4jRepositories.connect(driver, database).profiles.read_history(
        ObservationScopeId(f"scope_{target_id}")
    )

    assert [record.legacy_scope for record in rows] == [historical_id]
