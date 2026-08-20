"""Benchmark matrix execution through the Milestone 5 experiment lifecycle."""

from __future__ import annotations

import itertools
import resource
import time
from collections import defaultdict
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from typing import Any

from jaws.domain import (
    BenchmarkMatrixSpec,
    BenchmarkPartition,
    BenchmarkPolicy,
    BenchmarkReport,
    CanonicalDigest,
    ComponentSpec,
    DatasetId,
    DatasetManifest,
    EntityDefinition,
    EntityType,
    EvaluationResult,
    ExperimentSpec,
    ObservationWindow,
    PairedDelta,
    RankedFinding,
    RankerSpec,
    ReferenceKind,
    ReferenceSpec,
    RepresentationSpec,
    ResourceUsage,
    RewardVector,
    RunId,
    RunState,
    ScenarioBenchmarkResult,
    ScenarioData,
    ScenarioStatus,
    canonical_digest,
    primitive,
    ranked_findings_digest,
)

from .benchmark_metrics import (
    aggregate_metrics,
    coverage_for,
    evaluate_reward_vector,
    paired_deltas,
    stability_metrics,
)
from .benchmark_rankers import BenchmarkRanker, BenchmarkRegistries
from .research import ExperimentService


class BenchmarkDatasetRegistry:
    def __init__(self) -> None:
        self._manifests: dict[DatasetId, DatasetManifest] = {}
        self._scenarios: dict[tuple[DatasetId, str], ScenarioData] = {}

    def register(self, manifest: DatasetManifest, scenarios: Mapping[str, ScenarioData]) -> None:
        assert manifest.dataset_id is not None
        if manifest.dataset_id in self._manifests:
            raise ValueError(f"dataset already registered: {manifest.dataset_id}")
        expected = {item.scenario_id for item in manifest.scenarios}
        if set(scenarios) != expected:
            raise ValueError("scenario data does not match the dataset manifest")
        for scenario_id, data in scenarios.items():
            if data.manifest != next(
                item for item in manifest.scenarios if item.scenario_id == scenario_id
            ):
                raise ValueError("scenario data manifest differs from dataset manifest")
            self._scenarios[(manifest.dataset_id, scenario_id)] = data
        self._manifests[manifest.dataset_id] = manifest

    def manifest(self, dataset_id: DatasetId) -> DatasetManifest:
        try:
            return self._manifests[dataset_id]
        except KeyError as error:
            raise KeyError(f"unknown benchmark dataset: {dataset_id}") from error

    def scenario(self, dataset_id: DatasetId, scenario_id: str) -> ScenarioData:
        try:
            return self._scenarios[(dataset_id, scenario_id)]
        except KeyError as error:
            raise KeyError(f"unknown benchmark scenario: {dataset_id}/{scenario_id}") from error

    def tuning_manifests(self) -> tuple[DatasetManifest, ...]:
        return tuple(
            self._manifests[key].tuning_view()
            for key in sorted(self._manifests, key=lambda item: item.value)
        )


@dataclass(frozen=True, slots=True)
class CacheCompatibility:
    evidence_digest: CanonicalDigest
    entity_type: str
    representation_id: str
    representation_version: str
    feature_version: str
    template_version: str
    model_version: str
    normalization: str
    window: str

    @property
    def digest(self) -> CanonicalDigest:
        return canonical_digest(self)


class BenchmarkArtifactCache:
    def __init__(self) -> None:
        self._values: dict[CanonicalDigest, object] = {}
        self.hits = 0
        self.misses = 0

    def get_or_compute(self, key: CacheCompatibility, operation: Callable[[], object]) -> object:
        value = self._values.get(key.digest)
        if value is not None:
            self.hits += 1
            return value
        self.misses += 1
        value = operation()
        self._values[key.digest] = value
        return value


class BenchmarkResearchEngine:
    def __init__(
        self,
        scenario: ScenarioData,
        ranker: BenchmarkRanker,
        cache: BenchmarkArtifactCache,
    ) -> None:
        self.scenario = scenario
        self.ranker = ranker
        self.cache = cache
        self.reward: RewardVector | None = None
        self._started = 0.0

    def represent(self, specification: ExperimentSpec, variant: str) -> object:
        self._started = time.perf_counter()
        assert specification.representation is not None
        assert self.scenario.manifest.evidence_digest is not None
        parameters = specification.representation.parameters
        compatibility = CacheCompatibility(
            self.scenario.manifest.evidence_digest,
            specification.entity.entity_type.value,
            specification.representation.representation_id,
            specification.representation.version,
            str(parameters.get("feature_version", "1")),
            str(parameters.get("template_version", "none")),
            str(parameters.get("model_version", "none")),
            str(parameters.get("normalization", "none")),
            str(parameters.get("window", "full")),
        )
        return self.cache.get_or_compute(compatibility, lambda: tuple(self.scenario.candidates))

    def reference(
        self, specification: ExperimentSpec, variant: str, representation: object
    ) -> object:
        return {
            "kind": specification.reference.kind,
            "version": specification.reference.version,
            "candidate_count": len(self.scenario.candidates),
        }

    def rank(
        self,
        specification: ExperimentSpec,
        variant: str,
        representation: object,
        reference: object,
    ) -> tuple[RankedFinding, ...]:
        assert specification.ranker is not None
        candidates = tuple(self.scenario.candidates)
        return self.ranker.rank(candidates, specification.ranker)

    def evaluate(
        self,
        specification: ExperimentSpec,
        variant: str,
        run_id: RunId,
        findings: tuple[RankedFinding, ...],
    ) -> EvaluationResult:
        typed_findings = tuple(findings)
        usage = ResourceUsage(
            runtime_seconds=time.perf_counter() - self._started,
            peak_memory_bytes=int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss) * 1024,
        )
        fidelity = self.ranker.explanation_fidelity()
        if fidelity is None and self.ranker.metadata.explanation_capability != "none":
            fidelity = 1.0
        scalar_weights = specification.ranker.parameters.get("scalar_weights", {})  # type: ignore[union-attr]
        if not isinstance(scalar_weights, Mapping):
            raise ValueError("scalar_weights must be an object")
        self.reward = evaluate_reward_vector(
            typed_findings,
            self.scenario.manifest.labels,
            usage=usage,
            explanation_fidelity=fidelity,
            scalar_weights={str(key): float(value) for key, value in scalar_weights.items()},
        )
        evaluator = specification.evaluator
        return EvaluationResult(
            run_id=run_id,
            evaluator_id=evaluator.component_id,
            evaluator_version=evaluator.version,
            metrics=self.reward.flattened(),
            ranked_entity_ids=tuple(
                item.entity_id for item in typed_findings if item.entity_id is not None
            ),
            findings_digest=ranked_findings_digest(typed_findings),
            coverage={"completed": 1.0},
            details={
                "scenario_id": self.scenario.manifest.scenario_id,
                "reward_vector": primitive(self.reward),
            },
        )


class BenchmarkRunner:
    def __init__(
        self,
        datasets: BenchmarkDatasetRegistry,
        registries: BenchmarkRegistries,
        experiments: ExperimentService,
        policy: BenchmarkPolicy,
        *,
        cache: BenchmarkArtifactCache | None = None,
        environment: Mapping[str, object] | None = None,
    ) -> None:
        self.datasets = datasets
        self.registries = registries
        self.experiments = experiments
        self.policy = policy
        self.cache = cache or BenchmarkArtifactCache()
        self.environment = dict(environment or {})

    def run(
        self,
        matrix: BenchmarkMatrixSpec,
        *,
        allow_held_out_labels: bool = False,
    ) -> BenchmarkReport:
        assert matrix.experiment is not None
        results: list[ScenarioBenchmarkResult] = []
        axes = itertools.product(
            matrix.dataset_ids,
            matrix.scenario_ids,
            matrix.entity_types,
            matrix.representation_ids,
            matrix.reference_kinds,
            matrix.ranker_ids,
            matrix.parameter_sets,
            matrix.seeds,
            matrix.windows,
        )
        for (
            dataset_id,
            scenario_id,
            entity_type,
            representation_id,
            reference_kind,
            ranker_id,
            parameters,
            seed,
            window,
        ) in axes:
            manifest = self.datasets.manifest(dataset_id)
            try:
                scenario = self.datasets.scenario(dataset_id, scenario_id)
            except KeyError:
                results.append(
                    self._noncompleted(
                        dataset_id,
                        scenario_id,
                        ranker_id,
                        representation_id,
                        reference_kind,
                        seed,
                        window,
                        ScenarioStatus.MISSING,
                        "scenario is not part of this dataset",
                    )
                )
                continue
            if not scenario.manifest.available:
                results.append(
                    self._noncompleted(
                        dataset_id,
                        scenario_id,
                        ranker_id,
                        representation_id,
                        reference_kind,
                        seed,
                        window,
                        ScenarioStatus.MISSING,
                        "source capture is unavailable",
                    )
                )
                continue
            if (
                scenario.manifest.partition is BenchmarkPartition.HELD_OUT
                and not allow_held_out_labels
            ):
                results.append(
                    self._noncompleted(
                        dataset_id,
                        scenario_id,
                        ranker_id,
                        representation_id,
                        reference_kind,
                        seed,
                        window,
                        ScenarioStatus.SKIPPED,
                        "held-out labels withheld in routine tuning",
                    )
                )
                continue
            ranker = self.registries.rankers.resolve(ranker_id, "1")
            if ranker.metadata.requires_embedding and any(
                not item.embedding for item in scenario.candidates
            ):
                results.append(
                    self._noncompleted(
                        dataset_id,
                        scenario_id,
                        ranker_id,
                        representation_id,
                        reference_kind,
                        seed,
                        window,
                        ScenarioStatus.ABSTAINED,
                        "required embedding representation is unavailable",
                    )
                )
                continue
            specification = self._experiment_spec(
                matrix.experiment,
                manifest,
                scenario,
                entity_type,
                representation_id,
                reference_kind,
                ranker,
                parameters,
                seed,
                window,
            )
            engine = BenchmarkResearchEngine(scenario, ranker, self.cache)
            execution = self.experiments.execute(specification, "default", engine)
            if execution.run.state is not RunState.COMPLETED or engine.reward is None:
                results.append(
                    self._noncompleted(
                        dataset_id,
                        scenario_id,
                        ranker_id,
                        representation_id,
                        reference_kind,
                        seed,
                        window,
                        ScenarioStatus.FAILED,
                        execution.run.failure.message if execution.run.failure else "run failed",
                        run_id=execution.run.run_id,
                    )
                )
                continue
            assert execution.run.run_id is not None and execution.evaluation is not None
            results.append(
                ScenarioBenchmarkResult(
                    scenario_id=scenario_id,
                    scenario_family=scenario.manifest.family,
                    dataset_id=dataset_id,
                    run_id=execution.run.run_id,
                    artifact_digest=canonical_digest(execution.run.artifacts),
                    parameter_digest=canonical_digest(parameters),
                    ranker_id=ranker_id,
                    representation_id=representation_id,
                    reference_kind=reference_kind,
                    seed=seed,
                    window=window,
                    ranking=execution.evaluation.ranked_entity_ids,
                    reward=engine.reward,
                )
            )
        movement_results = self._attach_false_positive_movement(tuple(results))
        stable_results = self._attach_stability(movement_results)
        comparisons = self._comparisons(stable_results)
        checksums = {
            item.run_id.value: str(item.artifact_digest)
            for item in stable_results
            if item.run_id is not None and item.artifact_digest is not None
        }
        return BenchmarkReport(
            benchmark_id=f"benchmark-{canonical_digest(matrix)}",
            results=stable_results,
            aggregates=aggregate_metrics(stable_results),
            paired_deltas=comparisons,
            coverage=coverage_for(stable_results),
            required_baselines=self.policy.required_baselines,
            environment=self.environment,
            artifact_checksums=checksums,
        )

    @staticmethod
    def _experiment_spec(
        base: ExperimentSpec,
        dataset: DatasetManifest,
        scenario: ScenarioData,
        entity_type: EntityType,
        representation_id: str,
        reference_kind: ReferenceKind,
        ranker: BenchmarkRanker,
        parameters: Mapping[str, Any],
        seed: int,
        window: str,
    ) -> ExperimentSpec:
        assert dataset.dataset_id is not None
        assert dataset.checksum is not None
        assert scenario.manifest.evidence_digest is not None
        captures = scenario.manifest.capture_ids
        representation_parameters = {
            **(base.representation.parameters if base.representation else {}),
            "window": window,
        }
        return replace(
            base,
            hypothesis=f"Benchmark {scenario.manifest.scenario_id} with {ranker.metadata.ranker_id}",
            observation=ObservationWindow(capture_ids=captures, filters=(f"window:{window}",)),
            entity=EntityDefinition(entity_type=entity_type),
            representation=RepresentationSpec(
                representation_id=representation_id,
                version="1",
                parameters=representation_parameters,
            ),
            reference=ReferenceSpec(kind=reference_kind, version="1"),
            ranker=RankerSpec(
                ranker_id=ranker.metadata.ranker_id,
                version=ranker.metadata.version,
                direction=ranker.metadata.direction,
                seed=seed,
                parameters=parameters,
            ),
            evaluator=ComponentSpec(component_id="reward_vector", version="1"),
            renderer=ComponentSpec(component_id="json", version="1"),
            dataset_ids=(dataset.dataset_id,),
            evidence_digests={
                dataset.dataset_id.value: dataset.checksum,
                **{capture.value: scenario.manifest.evidence_digest for capture in captures},
            },
            label_source_versions={dataset.dataset_id.value: dataset.label_source_version},
        )

    @staticmethod
    def _noncompleted(
        dataset_id: DatasetId,
        scenario_id: str,
        ranker_id: str,
        representation_id: str,
        reference_kind: ReferenceKind,
        seed: int,
        window: str,
        status: ScenarioStatus,
        message: str,
        *,
        run_id: RunId | None = None,
    ) -> ScenarioBenchmarkResult:
        return ScenarioBenchmarkResult(
            scenario_id=scenario_id,
            scenario_family=scenario_id,
            dataset_id=dataset_id,
            run_id=run_id,
            ranker_id=ranker_id,
            representation_id=representation_id,
            reference_kind=reference_kind,
            seed=seed,
            window=window,
            status=status,
            message=message,
        )

    def _attach_false_positive_movement(
        self, results: tuple[ScenarioBenchmarkResult, ...]
    ) -> tuple[ScenarioBenchmarkResult, ...]:
        groups: dict[
            tuple[DatasetId | None, str, int, str, str, ReferenceKind],
            list[ScenarioBenchmarkResult],
        ] = defaultdict(list)
        for result in results:
            if result.status is ScenarioStatus.COMPLETED:
                groups[
                    (
                        result.dataset_id,
                        result.scenario_id,
                        result.seed,
                        result.window,
                        result.representation_id,
                        result.reference_kind,
                    )
                ].append(result)
        replacements: dict[RunId, RewardVector] = {}
        for rows in groups.values():
            control = next((row for row in rows if row.ranker_id == "seeded_random"), None)
            if control is None:
                continue
            assert control.dataset_id is not None
            scenario = self.datasets.scenario(control.dataset_id, control.scenario_id)
            benign = {label.entity_id for label in scenario.manifest.labels if label.relevance == 0}
            control_count = len(set(control.ranking[:3]) & benign)
            for row in rows:
                if row.run_id is None or row.reward is None:
                    continue
                movement = float(len(set(row.ranking[:3]) & benign) - control_count)
                replacements[row.run_id] = replace(
                    row.reward,
                    false_positive_movement={row.scenario_family: movement},
                )
        return tuple(
            replace(result, reward=replacements[result.run_id])
            if result.run_id in replacements
            else result
            for result in results
        )

    @staticmethod
    def _attach_stability(
        results: tuple[ScenarioBenchmarkResult, ...],
    ) -> tuple[ScenarioBenchmarkResult, ...]:
        groups: dict[
            tuple[DatasetId | None, str, str, str, ReferenceKind],
            list[ScenarioBenchmarkResult],
        ] = defaultdict(list)
        for result in results:
            if result.status is ScenarioStatus.COMPLETED:
                groups[
                    (
                        result.dataset_id,
                        result.scenario_id,
                        result.ranker_id,
                        result.representation_id,
                        result.reference_kind,
                    )
                ].append(result)
        replacements: dict[RunId, RewardVector] = {}
        for rows in groups.values():
            if len(rows) < 2:
                continue
            parameter_distance = (
                1.0 if rows[0].parameter_digest != rows[-1].parameter_digest else 0.0
            )
            stability = stability_metrics(
                rows[0].ranking,
                rows[-1].ranking,
                k=5,
                parameter_distance=parameter_distance,
            )
            for row in rows:
                assert row.run_id is not None and row.reward is not None
                replacements[row.run_id] = replace(row.reward, stability=stability)
        return tuple(
            replace(result, reward=replacements[result.run_id])
            if result.run_id in replacements
            else result
            for result in results
        )

    def _comparisons(self, results: tuple[ScenarioBenchmarkResult, ...]) -> tuple[PairedDelta, ...]:
        if not self.policy.required_baselines:
            return ()
        control_id = self.policy.required_baselines[0]
        control = tuple(item for item in results if item.ranker_id == control_id)
        rows: list[PairedDelta] = []
        for treatment_id in sorted({item.ranker_id for item in results} - {control_id}):
            treatment = tuple(item for item in results if item.ranker_id == treatment_id)
            rows.extend(paired_deltas(control, treatment))
        return tuple(rows)
