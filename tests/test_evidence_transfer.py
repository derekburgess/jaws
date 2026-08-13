"""Portable evidence bundle and transfer service behavior."""

from __future__ import annotations

import copy
import json
import stat
from types import SimpleNamespace

import pytest
from evidence_contract import (
    OBSERVED_AT,
    assert_evidence_transfer_contract,
    evidence_fixture,
    in_memory_source,
    in_memory_target,
    schema_provenance,
)

from jaws import evidence_cli
from jaws.adapters import (
    EvidenceBundleError,
    evidence_bundle_document,
    load_evidence_bundle,
    parse_evidence_bundle,
    write_evidence_bundle,
)
from jaws.domain import EvidenceBundle, EvidenceSchemaProvenance, SchemaMigrationProvenance
from jaws.ports import (
    EvidenceImportConflictError,
    EvidenceSchemaConflictError,
    FrozenClock,
    InMemoryEvidenceRepository,
)
from jaws.services import EvidenceTransferService


def test_in_memory_evidence_transfer_follows_shared_contract():
    assert_evidence_transfer_contract(in_memory_source(), in_memory_target())


def test_bundle_json_round_trip_and_checksum_tamper_detection(tmp_path):
    bundle = EvidenceBundle.build(evidence_fixture(), exported_at=OBSERVED_AT)
    path = tmp_path / "evidence.json"

    write_evidence_bundle(path, bundle)

    assert load_evidence_bundle(path) == bundle
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    with pytest.raises(EvidenceBundleError, match="overwrite"):
        write_evidence_bundle(path, bundle)

    document = copy.deepcopy(evidence_bundle_document(bundle))
    document["evidence"]["packets"][0]["payload"] = "tampered"
    with pytest.raises(EvidenceBundleError, match="checksum"):
        parse_evidence_bundle(document)


def test_import_rejects_different_schema_provenance():
    bundle = EvidenceBundle.build(evidence_fixture(), exported_at=OBSERVED_AT)
    incompatible = EvidenceSchemaProvenance(
        version=1,
        migrations=(SchemaMigrationProvenance(1, "different", "2" * 64),),
    )
    service = EvidenceTransferService(
        InMemoryEvidenceRepository(incompatible), FrozenClock(OBSERVED_AT)
    )

    with pytest.raises(EvidenceSchemaConflictError, match="does not match"):
        service.plan_import(bundle)


def test_apply_rejects_target_that_changed_after_dry_run():
    bundle = EvidenceBundle.build(evidence_fixture(), exported_at=OBSERVED_AT)
    target = InMemoryEvidenceRepository(schema_provenance())
    service = EvidenceTransferService(target, FrozenClock(OBSERVED_AT))
    plan = service.plan_import(bundle)
    target.evidence = evidence_fixture()

    with pytest.raises(EvidenceImportConflictError, match="changed after planning"):
        service.apply_import(bundle, plan)


def test_evidence_cli_exports_validates_dry_runs_and_imports(monkeypatch, capsys, tmp_path):
    path = tmp_path / "cli-evidence.json"
    source = in_memory_source()
    target = in_memory_target()
    selected = source
    monkeypatch.setattr(
        evidence_cli,
        "_repositories",
        lambda _database: SimpleNamespace(evidence=selected),
    )

    assert evidence_cli.main(("export", str(path), "--database", "source")) == 0
    export_output = json.loads(capsys.readouterr().out)
    assert export_output["manifest"]["record_counts"]["profiles"] == 2
    assert evidence_cli.main(("validate", str(path))) == 0
    assert json.loads(capsys.readouterr().out)["valid"] is True

    selected = target
    assert evidence_cli.main(("import", str(path), "--database", "target", "--dry-run")) == 0
    dry_run = json.loads(capsys.readouterr().out)
    assert dry_run["applied"] is False
    assert dry_run["plan"]["target_empty"] is True
    assert target.is_empty()

    assert evidence_cli.main(("import", str(path), "--database", "target")) == 0
    assert json.loads(capsys.readouterr().out)["applied"] is True
    assert target.snapshot() == source.snapshot()
