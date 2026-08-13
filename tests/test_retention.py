"""Offline retention policy, service, and CLI behavior."""

import json
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from retention_contract import assert_retention_service_contract

from jaws import retention_cli
from jaws.domain import (
    AuditContext,
    AuditEventId,
    ProfileStatus,
    RetentionMode,
    RetentionPolicy,
    RetentionResource,
    RetentionRule,
)
from jaws.ports import (
    FrozenClock,
    InMemoryCaptureRepository,
    InMemoryEnrichmentRepository,
    InMemoryInspectionRepository,
    InMemoryPacketRepository,
    InMemoryProfileRepository,
    SequenceIdGenerator,
)
from jaws.services import RetentionService, UnsupportedRetentionPolicyError


def _repositories():
    captures = InMemoryCaptureRepository()
    packets = InMemoryPacketRepository(captures)
    enrichment = InMemoryEnrichmentRepository(addresses=("192.0.2.10", "198.51.100.20"))
    profiles = InMemoryProfileRepository()
    return SimpleNamespace(
        captures=captures,
        packets=packets,
        enrichment=enrichment,
        profiles=profiles,
        inspection=InMemoryInspectionRepository(profiles, packets, enrichment, captures),
    )


def _service(repository):
    return RetentionService(
        repository,
        FrozenClock(datetime(2026, 8, 12, 12, tzinfo=UTC)),
        SequenceIdGenerator((AuditEventId("audit_test_retention"),)),
        AuditContext("test", "test-retention", "fixture"),
    )


def test_in_memory_retention_service_follows_shared_contract():
    assert_retention_service_contract(_repositories())


def test_policy_declares_all_resources_and_rejects_unsupported_deletion():
    policy = RetentionPolicy.legacy_profile_limit(20)
    assert {rule.resource for rule in policy.rules} == set(RetentionResource)
    assert policy.rule(RetentionResource.RAW_PACKETS).mode is RetentionMode.KEEP_ALL
    assert policy.rule(RetentionResource.PROFILE_SETS).keep_latest == 20
    with pytest.raises(ValueError, match="every resource"):
        RetentionPolicy("incomplete", "1", (RetentionRule(RetentionResource.PROFILE_SETS),))

    rules = tuple(
        RetentionRule(resource, RetentionMode.KEEP_LATEST, 1)
        if resource is RetentionResource.RAW_PACKETS
        else RetentionRule(resource)
        for resource in RetentionResource
    )
    unsupported = RetentionPolicy("unsafe", "1", rules)
    with pytest.raises(UnsupportedRetentionPolicyError, match="raw_packets"):
        _service(_repositories().profiles).plan(unsupported)


def test_quarantined_profiles_are_protected_from_profile_limit():
    repositories = _repositories()
    assert_retention_service_contract(repositories)
    remaining_scope = next(iter(repositories.profiles._records))
    records = repositories.profiles._records[remaining_scope]
    repositories.profiles._records[remaining_scope] = tuple(
        replace(record, status=ProfileStatus.LEGACY_QUARANTINED) for record in records
    )

    plan = _service(repositories.profiles).plan(RetentionPolicy.legacy_profile_limit(1))

    assert plan.protected_profile_scopes
    assert not plan.deleted_profile_scopes


def test_retention_cli_dry_run_serializes_plan_without_mutation(monkeypatch, capsys):
    repositories = _repositories()
    monkeypatch.setattr(
        retention_cli.importlib,
        "import_module",
        lambda _name: SimpleNamespace(DATABASE="fixture", get_neo4j_driver=lambda: object()),
    )
    monkeypatch.setattr(
        retention_cli.Neo4jRepositories,
        "connect",
        lambda _driver, _database: repositories,
    )

    assert retention_cli.main(("dry-run", "--retain-profiles", "20")) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["applied"] is False
    assert output["plan"]["policy"]["policy_id"] == "legacy_compute_profile_retention"
    assert output["deleted_profile_records"] == 0
