"""Offline contract checks for deterministic repository implementations."""

from types import SimpleNamespace

import pytest
from repository_contract import assert_capture_and_packet_repository_contract

from jaws.ports import (
    InMemoryCaptureRepository,
    InMemoryPacketRepository,
    RepositorySchemaError,
)
from jaws.storage import neo4j_repositories


def test_in_memory_capture_and_packet_repositories_follow_contract():
    captures = InMemoryCaptureRepository()
    repositories = type(
        "Repositories",
        (),
        {"captures": captures, "packets": InMemoryPacketRepository(captures)},
    )()

    assert_capture_and_packet_repository_contract(repositories)


def test_neo4j_repository_bundle_rejects_pending_or_drifted_schema(monkeypatch):
    status = SimpleNamespace(is_current=False, issues=("pending migration 2",))
    migration_manager = SimpleNamespace(validate=lambda: status)
    monkeypatch.setattr(
        neo4j_repositories,
        "manager",
        lambda driver, database: migration_manager,
    )

    with pytest.raises(RepositorySchemaError, match="pending migration 2"):
        neo4j_repositories.Neo4jRepositories.connect(object(), "captures")
