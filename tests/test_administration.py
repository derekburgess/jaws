"""Offline guarded-administration, confirmation, audit, and interface contracts."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from jaws import administration_cli
from jaws.domain import (
    AdministrationPlan,
    AdministrationResourceCount,
    AuditContext,
    AuditEventId,
    AuditOperation,
    EvidenceSchemaProvenance,
    SchemaMigrationProvenance,
)
from jaws.ports import (
    AdministrationConflictError,
    FrozenClock,
    InMemoryAdministrationRepository,
    SequenceIdGenerator,
)
from jaws.services import AdministrationConfirmationError, AdministrationService

NOW = datetime(2026, 8, 13, 15, 30, tzinfo=UTC)


def _plan(*, packets: int = 3) -> AdministrationPlan:
    schema = EvidenceSchemaProvenance(
        4,
        (
            SchemaMigrationProvenance(1, "adopt", "1" * 64),
            SchemaMigrationProvenance(2, "identity", "2" * 64),
            SchemaMigrationProvenance(3, "profiles", "3" * 64),
            SchemaMigrationProvenance(4, "audit", "4" * 64),
        ),
    )
    return AdministrationPlan(
        "disposable_fixture",
        schema,
        (
            AdministrationResourceCount("captures", 1),
            AdministrationResourceCount("packets", packets),
        ),
        relationship_count=5,
    )


def _service(repository: InMemoryAdministrationRepository) -> AdministrationService:
    return AdministrationService(
        repository,
        FrozenClock(NOW),
        SequenceIdGenerator((AuditEventId("audit_admin_fixture"),)),
        AuditContext("test_operator", "test-administration", "disposable_fixture"),
    )


def test_plan_is_read_only_and_confirmation_binds_operation_database_and_digest():
    repository = InMemoryAdministrationRepository(_plan())
    service = _service(repository)

    plan = service.plan()

    assert repository.plan() == plan
    assert plan.node_count == 4
    assert plan.confirmation == f"ERASE disposable_fixture {plan.digest}"
    assert repository.events == []


def test_erasure_requires_exact_confirmation_and_writes_minimal_audit_record():
    repository = InMemoryAdministrationRepository(_plan())
    service = _service(repository)
    plan = service.plan()

    with pytest.raises(AdministrationConfirmationError, match="exactly match"):
        service.erase(plan, f"ERASE disposable_fixture {'0' * 64}")
    assert repository.events == []
    assert repository.plan() == plan

    result = service.erase(plan, plan.confirmation)

    assert result.deleted_nodes == 4
    assert result.deleted_relationships == 5
    assert repository.plan().node_count == 0
    assert repository.events == [result.audit_event]
    assert result.audit_event.operation is AuditOperation.ERASE_MANAGED_EVIDENCE
    assert result.audit_event.context.target_database == "disposable_fixture"
    assert result.audit_event.affected_records == 4
    assert not ({"payload", "password", "secret"} & set(result.audit_event.__dataclass_fields__))


def test_changed_target_invalidates_an_already_confirmed_plan_without_erasure():
    repository = InMemoryAdministrationRepository(_plan())
    service = _service(repository)
    stale = service.plan()
    repository.current = _plan(packets=4)

    with pytest.raises(AdministrationConflictError, match="changed"):
        service.erase(stale, stale.confirmation)

    assert repository.plan().node_count == 5
    assert repository.events == []


def test_admin_cli_requires_an_explicit_database_and_serializes_a_plan(monkeypatch, capsys):
    with pytest.raises(SystemExit):
        administration_cli.main(("plan",))

    class Driver:
        closed = False

        def close(self):
            self.closed = True

    driver = Driver()
    repository = InMemoryAdministrationRepository(_plan())
    monkeypatch.setattr(
        administration_cli.importlib,
        "import_module",
        lambda _name: SimpleNamespace(get_neo4j_driver=lambda: driver),
    )
    monkeypatch.setattr(
        administration_cli.Neo4jRepositories,
        "connect",
        lambda _driver, _database: SimpleNamespace(administration=repository),
    )

    assert administration_cli.main(("plan", "--database", "disposable_fixture")) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["ok"] is True
    assert output["result"]["target_database"] == "disposable_fixture"
    assert output["result"]["confirmation"].startswith("ERASE disposable_fixture ")
    assert driver.closed


def test_agents_have_no_destructive_database_tool_or_legacy_unconfirmed_delete():
    mcp_source = Path("jaws_mcp/server.py").read_text(encoding="utf-8")
    utility_source = Path("jaws/jaws_utils.py").read_text(encoding="utf-8")

    assert 'name="drop_database"' not in mcp_source
    assert "def drop_database" not in mcp_source
    assert "DETACH DELETE" not in utility_source
    assert "default=DATABASE" not in utility_source
