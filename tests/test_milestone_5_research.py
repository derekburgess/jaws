from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from jaws.adapters.experiment_bundles import ExperimentBundleStore
from jaws.adapters.provenance import ProvenanceCollector
from jaws.domain import (
    ArtifactOrigin,
    CaptureId,
    ComponentSpec,
    DatasetId,
    EntityId,
    EvaluationResult,
    ExperimentRun,
    ExperimentSpec,
    FindingId,
    HypothesisOutcome,
    HypothesisSpec,
    ObservationWindow,
    OutlierStatus,
    RankedFinding,
    RankerSpec,
    ReferenceKind,
    ReferenceSpec,
    RepresentationSpec,
    RunId,
    RunState,
    Score,
    ScoreDirection,
    canonical_json,
    ranked_findings_digest,
)
from jaws.services.research import (
    ComponentDescriptor,
    ExperimentService,
    HypothesizeService,
    ObserveService,
    ResearchCatalog,
    ResearchRunRepository,
)

NOW = datetime(2026, 8, 20, 12, tzinfo=UTC)


def _spec() -> ExperimentSpec:
    return ExperimentSpec(
        hypothesis=HypothesisSpec(
            claim="treatment increases recall without exceeding the burden budget",
            control="control",
            treatments=("treatment",),
            metrics=("recall_at_3",),
            regression_budgets={"benign_burden": 0.0},
        ),
        observation=ObservationWindow(capture_ids=(CaptureId("capture-1"),)),
        representation=RepresentationSpec(representation_id="numeric", version="2"),
        reference=ReferenceSpec(kind=ReferenceKind.PEER, version="3"),
        ranker=RankerSpec(ranker_id="demo", version="4", seed=7),
        evaluator=ComponentSpec(component_id="metrics", version="5"),
        renderer=ComponentSpec(component_id="json", version="6"),
        dataset_ids=(DatasetId("dataset-1"),),
        label_source_versions={"truth": "1"},
        deterministic_settings={"threads": 1, "api_token": "must-not-leak"},
    )


def _catalog() -> ResearchCatalog:
    catalog = ResearchCatalog(
        datasets={"dataset-1"}, captures={"capture-1"}, labels={"truth:1"}
    )
    for descriptor in (
        ComponentDescriptor("representation", "numeric", "2"),
        ComponentDescriptor("reference", "peer", "3"),
        ComponentDescriptor("ranker", "demo", "4", compatible_representations=("numeric",)),
        ComponentDescriptor("evaluator", "metrics", "5"),
        ComponentDescriptor("renderer", "json", "6"),
    ):
        catalog.register(descriptor)
    return catalog


class _Engine:
    def represent(self, specification: ExperimentSpec, variant: str) -> object:
        return {"variant": variant, "values": [1.0, 2.0]}

    def reference(self, specification: ExperimentSpec, variant: str, representation: object) -> object:
        return {"median": 1.5, "representation": representation}

    def rank(
        self,
        specification: ExperimentSpec,
        variant: str,
        representation: object,
        reference: object,
    ) -> tuple[RankedFinding, ...]:
        entities = ("target", "benign") if variant == "treatment" else ("benign", "target")
        return tuple(
            RankedFinding(
                finding_id=FindingId(f"{variant}-{entity}"),
                entity_id=EntityId(entity),
                rank=rank,
                score=Score(3.0 - rank, ScoreDirection.HIGHER_IS_MORE_ANOMALOUS),
                outlier=OutlierStatus.OUTLIER if rank == 1 else OutlierStatus.INLIER,
            )
            for rank, entity in enumerate(entities, 1)
        )

    def evaluate(
        self,
        specification: ExperimentSpec,
        variant: str,
        run_id: RunId,
        findings: tuple[RankedFinding, ...],
    ) -> EvaluationResult:
        return EvaluationResult(
            run_id=run_id,
            evaluator_id="metrics",
            evaluator_version="5",
            metrics={
                "recall_at_3": 1.0 if variant == "treatment" else 0.5,
                "benign_burden": 0.0,
                "runtime_seconds": 2.0 if variant == "treatment" else 1.0,
            },
            ranked_entity_ids=tuple(item.entity_id for item in findings if item.entity_id),
            findings_digest=ranked_findings_digest(findings),
        )


class _CancelImmediately:
    def is_cancelled(self) -> bool:
        return True


def _service(tmp_path: Path) -> tuple[ExperimentService, ResearchRunRepository]:
    runs = ResearchRunRepository()
    ticks = iter(NOW + timedelta(seconds=index) for index in range(100))
    service = ExperimentService(
        _catalog(),
        runs,
        ExperimentBundleStore(tmp_path / "bundles"),
        ProvenanceCollector(Path.cwd()),
        now=lambda: next(ticks),
    )
    return service, runs


def test_versioned_identity_and_secret_free_serialization() -> None:
    specification = _spec()
    same = _spec()
    assert specification.digest == same.digest
    assert specification.experiment_id.value == str(specification.digest)
    serialized = canonical_json(specification)
    assert "must-not-leak" not in serialized
    assert '"api_token":"***redacted***"' in serialized
    assert '"schema_version":"1.0.0"' in serialized

    first = ExperimentRun.planned(specification.experiment_id, specification.digest, NOW)
    second = ExperimentRun.planned(specification.experiment_id, specification.digest, NOW)
    assert first.run_id != second.run_id
    assert first.experiment_id == second.experiment_id


def test_lifecycle_is_atomic_auditable_and_retry_links_runs(tmp_path: Path) -> None:
    service, runs = _service(tmp_path)

    class FailingEngine(_Engine):
        def reference(
            self, specification: ExperimentSpec, variant: str, representation: object
        ) -> object:
            raise RuntimeError("password=hunter2 provider unavailable")

    failed = service.execute(_spec(), "control", FailingEngine())
    assert failed.run.state is RunState.FAILED
    assert failed.run.completed_stages == ("representation",)
    assert failed.run.resumable
    assert failed.run.failure is not None
    assert "hunter2" not in failed.run.failure.message
    assert [event.to_state for event in failed.run.transitions] == [
        RunState.PLANNED,
        RunState.QUEUED,
        RunState.RUNNING,
        RunState.FAILED,
    ]

    retried = service.execute(_spec(), "control", _Engine(), retry_of=failed.run)
    assert retried.run.state is RunState.COMPLETED
    assert retried.run.retry_of_run_id == failed.run.run_id
    old = runs.get(failed.run.run_id)  # type: ignore[arg-type]
    assert old is not None and old.state is RunState.SUPERSEDED
    assert old.superseded_by_run_id == retried.run.run_id


def test_matrix_replay_cache_observation_and_offline_bundle(tmp_path: Path) -> None:
    service, _ = _service(tmp_path)
    specification = _spec()
    first = service.execute_matrix(specification, _Engine())
    replay = service.execute(specification, "treatment", _Engine())
    assert tuple(item.variant for item in first) == ("control", "treatment")
    assert all(item.run.state is RunState.COMPLETED for item in first)
    assert first[1].evaluation is not None and replay.evaluation is not None
    assert first[1].evaluation.analytical_digest == replay.evaluation.analytical_digest
    assert any(record.origin is ArtifactOrigin.CACHED for record in replay.run.artifacts)

    store = ExperimentBundleStore(tmp_path / "bundles")
    verification = store.verify(Path(replay.bundle))
    inspection = store.inspect(Path(replay.bundle))
    assert verification.valid
    assert inspection.manifest["state"] == "completed"
    assert inspection.manifest["specification_digest"] == str(specification.digest)
    bundle_text = "".join(
        path.read_text(errors="ignore") for path in Path(replay.bundle).rglob("*") if path.is_file()
    )
    assert "must-not-leak" not in bundle_text

    control = first[0].evaluation
    treatment = first[1].evaluation
    assert control is not None and treatment is not None
    observation = ObserveService().observe(specification, control, treatment)
    assert observation.metric_deltas == {"benign_burden": 0.0, "recall_at_3": 0.5}
    assert observation.cost_deltas == {"runtime_seconds": 1.0}
    assert observation.outcome is HypothesisOutcome.SUPPORTED
    with_interpretation = ObserveService().observe(
        specification, control, treatment, human_interpretation="promising"
    )
    assert observation.deterministic_digest == with_interpretation.deterministic_digest


def test_cancellation_failure_and_corruption_never_look_complete(tmp_path: Path) -> None:
    service, _ = _service(tmp_path)
    execution = service.execute(
        _spec(), "control", _Engine(), cancellation=_CancelImmediately()
    )
    assert execution.run.state is RunState.CANCELLED
    assert execution.evaluation is None
    store = ExperimentBundleStore(tmp_path / "bundles")
    assert store.verify(Path(execution.bundle)).valid
    assert store.inspect(Path(execution.bundle)).manifest["state"] == "cancelled"

    artifact = Path(execution.bundle) / "run/variant.json"
    artifact.write_text("corrupted", encoding="utf-8")
    verification = store.verify(Path(execution.bundle))
    assert not verification.valid
    assert verification.mismatched == ("run/variant.json",)


def test_export_import_excludes_raw_capture_and_gc_protects_references(tmp_path: Path) -> None:
    service, _ = _service(tmp_path / "source")
    execution = service.execute(_spec(), "control", _Engine(), cancellation=_CancelImmediately())
    source_store = ExperimentBundleStore(tmp_path / "source/bundles")
    archive = tmp_path / "run.tar.gz"
    source_store.export_bundle(Path(execution.bundle), archive)
    imported_store = ExperimentBundleStore(tmp_path / "imported")
    imported = imported_store.import_bundle(archive)
    assert imported_store.verify(imported).valid
    assert not any(path.suffix in {".pcap", ".pcapng"} for path in imported.rglob("*"))

    with pytest.raises(ValueError, match="never embed raw captures"):
        run = ExperimentRun.planned(_spec().experiment_id, _spec().digest, NOW)
        source_store.write(_spec(), run, {"raw/evidence.pcap": b"pcap"})

    plan = imported_store.garbage_collect()
    assert plan.removable == (imported,)
    assert imported.exists()
    removed = imported_store.garbage_collect(execute=True)
    assert removed.removed == (imported,)
    assert not imported.exists()


def test_catalog_and_hypothesis_validation_precede_execution() -> None:
    specification = _spec()
    _catalog().validate(specification)
    assert HypothesizeService().validate(specification.hypothesis) is specification.hypothesis
    incompatible = _catalog()
    incompatible.components[("ranker", "demo", "4")] = ComponentDescriptor(
        "ranker", "demo", "4", compatible_representations=("text",)
    )
    with pytest.raises(ValueError, match="incompatible"):
        incompatible.validate(specification)
    payload = json.loads(canonical_json(incompatible.snapshot()))
    assert payload["datasets"] == ["dataset-1"]
