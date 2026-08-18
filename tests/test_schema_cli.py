"""Schema CLI backup-proof orchestration without database access."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace

from jaws.domain import CanonicalDigest
from jaws.storage import cli
from jaws.storage.migrations import MigrationPlan, MigrationResult, SchemaStatus


class FakeManager:
    def __init__(self, plan: MigrationPlan) -> None:
        self.plan = plan
        self.proofs = []

    def dry_run(self) -> MigrationPlan:
        return self.plan

    def migrate(self, proof=None) -> MigrationResult:
        self.proofs.append(proof)
        return MigrationResult(
            self.plan.backup_required_versions,
            SchemaStatus(
                self.plan.target_version,
                self.plan.target_version,
                (),
                (),
                (),
            ),
        )


def _plan() -> MigrationPlan:
    return MigrationPlan(
        current_version=4,
        target_version=5,
        pending=(),
        statements=(),
        source_schema_digest=CanonicalDigest("s" * 64),
        backup_required_versions=(5,),
    )


def test_schema_cli_fails_before_migration_when_required_backup_is_missing(monkeypatch, capsys):
    fake = FakeManager(_plan())
    monkeypatch.setattr(
        cli.importlib,
        "import_module",
        lambda _name: SimpleNamespace(DATABASE="captures", get_neo4j_driver=lambda: object()),
    )
    monkeypatch.setattr(cli, "manager", lambda _driver, _database: fake)

    assert cli.main(("migrate", "--database", "captures")) == 1
    assert "verified evidence backup required" in json.loads(capsys.readouterr().err)["error"]
    assert fake.proofs == []


def test_schema_cli_verifies_bundle_and_passes_only_returned_proof(monkeypatch, capsys, tmp_path):
    fake = FakeManager(_plan())
    proof = SimpleNamespace(exported_at=datetime(2026, 8, 13, tzinfo=UTC))
    driver = object()
    repository = object()
    bundle_path = tmp_path / "evidence.json"
    calls = []
    monkeypatch.setattr(
        cli.importlib,
        "import_module",
        lambda _name: SimpleNamespace(DATABASE="captures", get_neo4j_driver=lambda: driver),
    )
    monkeypatch.setattr(cli, "manager", lambda actual, database: fake)
    monkeypatch.setattr(cli, "Neo4jEvidenceRepository", lambda actual, database: repository)
    monkeypatch.setattr(
        cli,
        "verify_migration_backup",
        lambda path, **kwargs: calls.append((path, kwargs)) or proof,
    )

    assert cli.main(("migrate", "--database", "captures", "--backup", str(bundle_path))) == 0

    assert fake.proofs == [proof]
    assert calls == [
        (
            bundle_path,
            {"database": "captures", "plan": fake.plan, "evidence": repository},
        )
    ]
    assert json.loads(capsys.readouterr().out)["applied_versions"] == [5]
