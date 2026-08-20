"""Orient, Hypothesize, Experiment, and Observe application services."""

from __future__ import annotations

import hashlib
import resource
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from jaws.domain import (
    ArtifactOrigin,
    ArtifactRecord,
    CanonicalDigest,
    ErrorCategory,
    EvaluationResult,
    ExperimentRun,
    ExperimentRunIndex,
    ExperimentSpec,
    HypothesisOutcome,
    HypothesisSpec,
    ObservationReport,
    ProvenanceRecord,
    RankedFinding,
    RankMovement,
    ResourceUsage,
    RunFailure,
    RunId,
    RunState,
    canonical_digest,
    canonical_json,
)
from jaws.ports import CancellationSignal, ExperimentIndexRepository


@dataclass(frozen=True, slots=True)
class ComponentDescriptor:
    kind: str
    component_id: str
    version: str
    schema_version: str = "1.0.0"
    compatible_representations: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CatalogSnapshot:
    datasets: tuple[str, ...]
    captures: tuple[str, ...]
    labels: tuple[str, ...]
    components: tuple[ComponentDescriptor, ...]
    prior_experiments: tuple[str, ...] = ()
    benchmark_summaries: tuple[Mapping[str, Any], ...] = ()


@dataclass(slots=True)
class ResearchCatalog:
    datasets: set[str] = field(default_factory=set)
    captures: set[str] = field(default_factory=set)
    labels: set[str] = field(default_factory=set)
    components: dict[tuple[str, str, str], ComponentDescriptor] = field(default_factory=dict)
    prior_experiments: set[str] = field(default_factory=set)
    benchmark_summaries: list[Mapping[str, Any]] = field(default_factory=list)

    def register(self, descriptor: ComponentDescriptor) -> None:
        key = (descriptor.kind, descriptor.component_id, descriptor.version)
        if key in self.components:
            raise ValueError(f"component already registered: {key}")
        self.components[key] = descriptor

    def validate(self, specification: ExperimentSpec) -> None:
        assert specification.observation is not None
        assert specification.representation is not None
        assert specification.ranker is not None
        missing_datasets = sorted(
            item.value for item in specification.dataset_ids if item.value not in self.datasets
        )
        missing_captures = sorted(
            item.value
            for item in specification.observation.capture_ids
            if item.value not in self.captures
        )
        if missing_datasets or missing_captures:
            raise ValueError(
                f"unavailable experiment evidence: datasets={missing_datasets}, captures={missing_captures}"
            )
        if isinstance(specification.hypothesis, HypothesisSpec):
            evidence_ids = {
                *(item.value for item in specification.dataset_ids),
                *(item.value for item in specification.observation.capture_ids),
            }
            missing_digests = sorted(evidence_ids - set(specification.evidence_digests))
            missing_labels = sorted(
                f"{name}:{version}"
                for name, version in specification.label_source_versions.items()
                if f"{name}:{version}" not in self.labels
            )
            if missing_digests or missing_labels:
                raise ValueError(
                    f"incomplete evidence provenance: digests={missing_digests}, labels={missing_labels}"
                )
        requested = (
            (
                "representation",
                specification.representation.representation_id,
                specification.representation.version,
            ),
            ("reference", specification.reference.kind.value, specification.reference.version),
            ("ranker", specification.ranker.ranker_id, specification.ranker.version),
            ("evaluator", specification.evaluator.component_id, specification.evaluator.version),
            ("renderer", specification.renderer.component_id, specification.renderer.version),
        )
        resolved: dict[str, ComponentDescriptor] = {}
        for key in requested:
            descriptor = self.components.get(key)
            if descriptor is None:
                raise ValueError(f"unavailable versioned component: {key}")
            resolved[key[0]] = descriptor
        supported = resolved["ranker"].compatible_representations
        if supported and specification.representation.representation_id not in supported:
            raise ValueError("ranker and representation are incompatible")

    def snapshot(self) -> CatalogSnapshot:
        return CatalogSnapshot(
            tuple(sorted(self.datasets)),
            tuple(sorted(self.captures)),
            tuple(sorted(self.labels)),
            tuple(
                sorted(
                    self.components.values(),
                    key=lambda item: (item.kind, item.component_id, item.version),
                )
            ),
            tuple(sorted(self.prior_experiments)),
            tuple(self.benchmark_summaries),
        )


class OrientService:
    def __init__(self, catalog: ResearchCatalog) -> None:
        self.catalog = catalog

    def orient(self) -> CatalogSnapshot:
        return self.catalog.snapshot()


class HypothesizeService:
    def validate(self, hypothesis: HypothesisSpec) -> HypothesisSpec:
        if hypothesis.claim.endswith("?"):
            raise ValueError("hypothesis must state a falsifiable claim, not a question")
        for metric, budget in hypothesis.regression_budgets.items():
            if not isinstance(budget, (int, float)) or float(budget) < 0:
                raise ValueError(f"regression budget must be a non-negative number: {metric}")
        return hypothesis


class ResearchRunRepository:
    """Thread-safe optimistic repository for atomic lifecycle snapshots."""

    def __init__(self, snapshot_writer: Callable[[ExperimentRun], None] | None = None) -> None:
        self._runs: dict[RunId, ExperimentRun] = {}
        self._lock = threading.Lock()
        self._snapshot_writer = snapshot_writer

    def add(self, run: ExperimentRun) -> None:
        assert run.run_id is not None
        with self._lock:
            if run.run_id in self._runs:
                raise ValueError(f"run already exists: {run.run_id}")
            if run.state is not RunState.PLANNED:
                raise ValueError("new run must be planned")
            if self._snapshot_writer is not None:
                self._snapshot_writer(run)
            self._runs[run.run_id] = run

    def get(self, run_id: RunId) -> ExperimentRun | None:
        with self._lock:
            return self._runs.get(run_id)

    def transition(self, run: ExperimentRun, *, expected: RunState) -> None:
        assert run.run_id is not None
        with self._lock:
            current = self._runs.get(run.run_id)
            if current is None:
                raise KeyError(str(run.run_id))
            if current.state is not expected:
                raise ValueError(f"run state conflict: expected {expected}, found {current.state}")
            if len(run.transitions) != len(current.transitions) + 1:
                raise ValueError("run transition must append exactly one audit event")
            immutable = (run.run_id, run.experiment_id, run.specification_digest, run.created_at)
            current_immutable = (
                current.run_id,
                current.experiment_id,
                current.specification_digest,
                current.created_at,
            )
            if immutable != current_immutable:
                raise ValueError("run identity metadata cannot change")
            if self._snapshot_writer is not None:
                self._snapshot_writer(run)
            self._runs[run.run_id] = run

    def list(self) -> tuple[ExperimentRun, ...]:
        with self._lock:
            return tuple(
                sorted(
                    self._runs.values(),
                    key=lambda item: item.created_at or datetime.min.replace(tzinfo=UTC),
                )
            )


class ResearchEngine(Protocol):
    def represent(self, specification: ExperimentSpec, variant: str) -> object: ...

    def reference(
        self, specification: ExperimentSpec, variant: str, representation: object
    ) -> object: ...

    def rank(
        self,
        specification: ExperimentSpec,
        variant: str,
        representation: object,
        reference: object,
    ) -> tuple[RankedFinding, ...]: ...

    def evaluate(
        self,
        specification: ExperimentSpec,
        variant: str,
        run_id: RunId,
        findings: tuple[RankedFinding, ...],
    ) -> EvaluationResult: ...


class ExperimentBundleWriter(Protocol):
    def write(
        self,
        specification: ExperimentSpec,
        run: ExperimentRun,
        artifacts: Mapping[str, bytes],
        *,
        referenced_run_ids: Sequence[RunId] = (),
    ) -> tuple[Path, CanonicalDigest]: ...


class ProvenanceProvider(Protocol):
    def collect(
        self,
        specification: ExperimentSpec,
        *,
        evidence_digests: Mapping[str, CanonicalDigest] | None = None,
        usage: ResourceUsage | None = None,
    ) -> ProvenanceRecord: ...


@dataclass(frozen=True, slots=True)
class _CachedStage:
    value: object
    content: bytes
    source_run_id: RunId


class StageCache:
    def __init__(self) -> None:
        self._values: dict[CanonicalDigest, _CachedStage] = {}

    def get(self, key: CanonicalDigest) -> _CachedStage | None:
        return self._values.get(key)

    def put(self, key: CanonicalDigest, value: _CachedStage) -> None:
        self._values.setdefault(key, value)


class _NeverCancelled:
    def is_cancelled(self) -> bool:
        return False


class ExperimentCancelled(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ExperimentExecution:
    variant: str
    run: ExperimentRun
    evaluation: EvaluationResult | None
    bundle: str


class ExperimentService:
    STAGES = ("representation", "reference", "ranking", "evaluation")

    def __init__(
        self,
        catalog: ResearchCatalog,
        runs: ResearchRunRepository,
        bundles: ExperimentBundleWriter,
        provenance: ProvenanceProvider,
        *,
        cache: StageCache | None = None,
        now: Callable[[], datetime] | None = None,
        cancellation_factory: Callable[[RunId], CancellationSignal] | None = None,
        index: ExperimentIndexRepository | None = None,
    ) -> None:
        self.catalog = catalog
        self.runs = runs
        self.bundles = bundles
        self.provenance = provenance
        self.cache = cache or StageCache()
        self.now = now or (lambda: datetime.now(UTC))
        self.cancellation_factory = cancellation_factory
        self.index = index

    def execute_matrix(
        self,
        specification: ExperimentSpec,
        engine: ResearchEngine,
        *,
        cancellation: CancellationSignal | None = None,
    ) -> tuple[ExperimentExecution, ...]:
        self.catalog.validate(specification)
        variants = self._variants(specification)
        return tuple(
            self.execute(specification, variant, engine, cancellation=cancellation)
            for variant in variants
        )

    def execute(
        self,
        specification: ExperimentSpec,
        variant: str,
        engine: ResearchEngine,
        *,
        cancellation: CancellationSignal | None = None,
        retry_of: ExperimentRun | None = None,
    ) -> ExperimentExecution:
        self.catalog.validate(specification)
        if variant not in self._variants(specification):
            raise ValueError(f"variant is not declared by the hypothesis: {variant}")
        if retry_of is not None and not retry_of.resumable:
            raise ValueError("only resumable failed runs may be retried")
        run = ExperimentRun.planned(
            specification.experiment_id,
            specification.digest,
            self.now(),
            retry_of_run_id=retry_of.run_id if retry_of else None,
        )
        self.runs.add(run)
        if self.index is not None:
            assert run.run_id is not None and run.created_at is not None
            self.index.add(
                ExperimentRunIndex(
                    run.run_id,
                    specification.experiment_id,
                    specification.digest,
                    RunState.PLANNED,
                    run.created_at,
                    supersedes_run_id=retry_of.run_id if retry_of else None,
                )
            )
        run = self._transition(run, RunState.QUEUED)
        run = self._transition(run, RunState.RUNNING)
        assert run.run_id is not None
        signal = cancellation or (
            self.cancellation_factory(run.run_id)
            if self.cancellation_factory is not None
            else _NeverCancelled()
        )
        started = time.perf_counter()
        artifacts: dict[str, bytes] = {}
        records: list[ArtifactRecord] = []
        completed: list[str] = []
        evaluation: EvaluationResult | None = None
        current: object | None = None
        reference: object | None = None
        findings: tuple[RankedFinding, ...] = ()
        try:
            self._check_cancelled(signal)
            current, record, content = self._stage(
                specification,
                variant,
                run.run_id,
                "representation",
                (),
                lambda: engine.represent(specification, variant),
            )
            artifacts[record.logical_path] = content
            records.append(record)
            completed.append("representation")
            self._check_cancelled(signal)
            reference, record, content = self._stage(
                specification,
                variant,
                run.run_id,
                "reference",
                (canonical_digest(current),),
                lambda: engine.reference(specification, variant, current),
            )
            artifacts[record.logical_path] = content
            records.append(record)
            completed.append("reference")
            self._check_cancelled(signal)
            ranked, record, content = self._stage(
                specification,
                variant,
                run.run_id,
                "ranking",
                (canonical_digest(current), canonical_digest(reference)),
                lambda: engine.rank(specification, variant, current, reference),
            )
            if not isinstance(ranked, tuple) or any(
                not isinstance(item, RankedFinding) for item in ranked
            ):
                raise TypeError("engine rank stage must return RankedFinding tuple")
            findings = ranked
            artifacts[record.logical_path] = content
            records.append(record)
            completed.append("ranking")
            self._check_cancelled(signal)
            assert run.run_id is not None
            evaluation = engine.evaluate(specification, variant, run.run_id, findings)
            if evaluation.run_id != run.run_id:
                raise ValueError("evaluation run ID does not match executing run")
            evaluation_content = (canonical_json(evaluation) + "\n").encode()
            evaluation_record = self._record(
                f"artifacts/{variant}/evaluation.json", evaluation_content
            )
            artifacts[evaluation_record.logical_path] = evaluation_content
            records.append(evaluation_record)
            completed.append("evaluation")
            usage = ResourceUsage(
                runtime_seconds=time.perf_counter() - started,
                peak_memory_bytes=int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss) * 1024,
                peak_gpu_memory_bytes=self._optional_int_metric(
                    evaluation.metrics, "peak_gpu_memory_bytes"
                ),
                external_api_calls=self._optional_int_metric(
                    evaluation.metrics, "external_api_calls"
                )
                or 0,
                external_api_tokens=self._optional_int_metric(
                    evaluation.metrics, "external_api_tokens"
                )
                or 0,
                estimated_cost=evaluation.metrics.get("estimated_cost"),
            )
            provenance = self.provenance.collect(specification, usage=usage)
            artifacts["provenance/provenance.json"] = (canonical_json(provenance) + "\n").encode()
            run = self._transition(
                run,
                RunState.COMPLETED,
                completed_stages=tuple(completed),
                artifacts=tuple(records),
                provenance=provenance,
            )
        except ExperimentCancelled as error:
            run = self._transition(
                run,
                RunState.CANCELLED,
                reason=str(error),
                completed_stages=tuple(completed),
                artifacts=tuple(records),
            )
        except Exception as error:
            failure = RunFailure(ErrorCategory.EXPERIMENT, str(error), resumable=True)
            run = self._transition(
                run,
                RunState.FAILED,
                reason="experiment stage failed",
                failure=failure,
                completed_stages=tuple(completed),
                artifacts=tuple(records),
            )
        artifacts["run/variant.json"] = (canonical_json({"variant": variant}) + "\n").encode()
        bundle, bundle_digest = self.bundles.write(specification, run, artifacts)
        self._complete_index(run, bundle.resolve().as_uri(), bundle_digest)
        if retry_of is not None and run.state is RunState.COMPLETED:
            assert run.run_id is not None
            superseded = retry_of.transition(
                RunState.SUPERSEDED,
                self.now(),
                reason="successful retry",
                superseded_by_run_id=run.run_id,
            )
            self.runs.transition(superseded, expected=RunState.FAILED)
            self._supersede_index(superseded)
        return ExperimentExecution(variant, run, evaluation, str(bundle))

    def cancel(self, run_id: RunId) -> ExperimentRun:
        run = self.runs.get(run_id)
        if run is None:
            raise KeyError(str(run_id))
        if run.state not in {RunState.PLANNED, RunState.QUEUED, RunState.RUNNING}:
            raise ValueError("only active runs may be cancelled")
        return self._transition(run, RunState.CANCELLED, reason="cancel requested")

    def _transition(self, run: ExperimentRun, target: RunState, **kwargs: Any) -> ExperimentRun:
        updated = run.transition(target, self.now(), **kwargs)
        self.runs.transition(updated, expected=run.state)
        if self.index is not None and target in {RunState.QUEUED, RunState.RUNNING}:
            indexed = self.index.get(updated.run_id)  # type: ignore[arg-type]
            if indexed is None:
                raise KeyError(f"run index not found: {updated.run_id}")
            transitioned = indexed.transition(target, updated.transitions[-1].at)
            self.index.transition(transitioned, expected_state=run.state)
        return updated

    def _complete_index(
        self, run: ExperimentRun, artifact_uri: str, artifact_digest: CanonicalDigest
    ) -> None:
        if self.index is None:
            return
        assert run.run_id is not None
        indexed = self.index.get(run.run_id)
        if indexed is None:
            raise KeyError(f"run index not found: {run.run_id}")
        transitioned = indexed.transition(
            run.state,
            run.transitions[-1].at,
            artifact_uri=artifact_uri,
            artifact_digest=artifact_digest,
            failure_code=(run.failure.category.value if run.failure is not None else None),
        )
        self.index.transition(transitioned, expected_state=indexed.state)

    def _supersede_index(self, run: ExperimentRun) -> None:
        if self.index is None:
            return
        assert run.run_id is not None
        indexed = self.index.get(run.run_id)
        if indexed is None:
            raise KeyError(f"run index not found: {run.run_id}")
        assert indexed.artifact_uri is not None and indexed.artifact_digest is not None
        transitioned = indexed.transition(
            RunState.SUPERSEDED,
            run.transitions[-1].at,
            artifact_uri=indexed.artifact_uri,
            artifact_digest=indexed.artifact_digest,
        )
        self.index.transition(transitioned, expected_state=indexed.state)

    def _stage(
        self,
        specification: ExperimentSpec,
        variant: str,
        run_id: RunId | None,
        stage: str,
        inputs: tuple[CanonicalDigest, ...],
        operation: Callable[[], object],
    ) -> tuple[object, ArtifactRecord, bytes]:
        assert run_id is not None
        key = canonical_digest(
            {
                "specification_digest": specification.digest,
                "variant": variant,
                "stage": stage,
                "inputs": inputs,
            }
        )
        cached = self.cache.get(key)
        path = f"artifacts/{variant}/{stage}.json"
        if cached is not None:
            return (
                cached.value,
                ArtifactRecord(
                    path,
                    self._bytes_digest(cached.content),
                    "application/json",
                    ArtifactOrigin.CACHED,
                    cached.source_run_id,
                ),
                cached.content,
            )
        value = operation()
        content = (canonical_json(value) + "\n").encode()
        self.cache.put(key, _CachedStage(value, content, run_id))
        return value, self._record(path, content), content

    @staticmethod
    def _record(path: str, content: bytes) -> ArtifactRecord:
        return ArtifactRecord(path, ExperimentService._bytes_digest(content), "application/json")

    @staticmethod
    def _bytes_digest(content: bytes) -> CanonicalDigest:
        return CanonicalDigest(hashlib.sha256(content).hexdigest())

    @staticmethod
    def _optional_int_metric(metrics: Mapping[str, float], key: str) -> int | None:
        value = metrics.get(key)
        return int(value) if value is not None else None

    @staticmethod
    def _check_cancelled(signal: CancellationSignal) -> None:
        if signal.is_cancelled():
            raise ExperimentCancelled("experiment cancelled between stages")

    @staticmethod
    def _variants(specification: ExperimentSpec) -> tuple[str, ...]:
        if isinstance(specification.hypothesis, HypothesisSpec):
            return (specification.hypothesis.control, *specification.hypothesis.treatments)
        return ("default",)


class ObserveService:
    COST_METRICS = frozenset({"runtime_seconds", "estimated_cost", "external_api_calls"})

    def observe(
        self,
        specification: ExperimentSpec,
        control: EvaluationResult,
        treatment: EvaluationResult,
        *,
        human_interpretation: str | None = None,
    ) -> ObservationReport:
        if not isinstance(specification.hypothesis, HypothesisSpec):
            raise ValueError("control/treatment observation requires a structured hypothesis")
        metrics = sorted(set(control.metrics) | set(treatment.metrics))
        deltas = {
            metric: treatment.metrics.get(metric, 0.0) - control.metrics.get(metric, 0.0)
            for metric in metrics
            if metric not in self.COST_METRICS
        }
        costs = {
            metric: treatment.metrics.get(metric, 0.0) - control.metrics.get(metric, 0.0)
            for metric in metrics
            if metric in self.COST_METRICS
        }
        regressions = tuple(
            metric
            for metric, budget in specification.hypothesis.regression_budgets.items()
            if deltas.get(metric, 0.0) < -float(budget)
        )
        control_ranks = {entity: rank for rank, entity in enumerate(control.ranked_entity_ids, 1)}
        treatment_ranks = {
            entity: rank for rank, entity in enumerate(treatment.ranked_entity_ids, 1)
        }
        entities = sorted(set(control_ranks) | set(treatment_ranks), key=lambda item: item.value)
        movements = tuple(
            RankMovement(entity, control_ranks.get(entity), treatment_ranks.get(entity))
            for entity in entities
        )
        primary = tuple(deltas.get(metric, 0.0) for metric in specification.hypothesis.metrics)
        if regressions:
            outcome = HypothesisOutcome.REFUTED
        elif primary and all(delta > 0 for delta in primary):
            outcome = HypothesisOutcome.SUPPORTED
        elif primary and any(delta < 0 for delta in primary):
            outcome = HypothesisOutcome.REFUTED
        else:
            outcome = HypothesisOutcome.INCONCLUSIVE
        assert control.run_id is not None and treatment.run_id is not None
        observation_id = f"observation-{canonical_digest((control.analytical_digest, treatment.analytical_digest))}"
        return ObservationReport(
            observation_id=observation_id,
            experiment_id=specification.experiment_id,
            control_run_id=control.run_id,
            treatment_run_id=treatment.run_id,
            metric_deltas=deltas,
            rank_movements=movements,
            regressions=regressions,
            cost_deltas=costs,
            outcome=outcome,
            rationale=(
                f"primary metric deltas: {', '.join(f'{item:.6g}' for item in primary)}",
                f"regressions beyond budget: {', '.join(regressions) if regressions else 'none'}",
            ),
            human_interpretation=human_interpretation,
        )


def compare_analytical_results(
    left: EvaluationResult, right: EvaluationResult
) -> Mapping[str, object]:
    """Stable replay comparison excluding runtime-only run identities."""

    return {
        "identical": left.analytical_digest == right.analytical_digest,
        "left_digest": str(left.analytical_digest),
        "right_digest": str(right.analytical_digest),
        "metric_deltas": {
            key: right.metrics.get(key, 0.0) - left.metrics.get(key, 0.0)
            for key in sorted(set(left.metrics) | set(right.metrics))
        },
        "ranking_identical": left.ranked_entity_ids == right.ranked_entity_ids,
        "findings_digest_identical": left.findings_digest == right.findings_digest,
    }
