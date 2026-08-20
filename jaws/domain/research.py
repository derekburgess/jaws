"""Immutable experiment execution, provenance, and observation records."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Any
from uuid import uuid4

from .enums import ErrorCategory, RunState
from .identifiers import CanonicalDigest, EntityId, ExperimentId, RunId, SchemaVersion
from .serialization import canonical_digest, redact_text
from .specifications import RankedFinding, VersionedSpec
from .time import RUN_TRANSITIONS, normalize_utc, require_transition

MetricValues = Mapping[str, float]
Metadata = Mapping[str, Any]


def _mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    return MappingProxyType({str(key): _immutable(item) for key, item in value.items()})


def _immutable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return _mapping(value)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return tuple(_immutable(item) for item in value)
    return value


def _metrics(value: MetricValues) -> MetricValues:
    normalized = {str(key).strip(): float(item) for key, item in value.items()}
    if any(not key for key in normalized):
        raise ValueError("metric names cannot be empty")
    if any(not float("-inf") < item < float("inf") for item in normalized.values()):
        raise ValueError("metric values must be finite")
    return MappingProxyType(normalized)


class ArtifactOrigin(StrEnum):
    COMPUTED = "computed"
    CACHED = "cached"
    REUSED = "reused"


class HypothesisOutcome(StrEnum):
    SUPPORTED = "supported"
    REFUTED = "refuted"
    INCONCLUSIVE = "inconclusive"


@dataclass(frozen=True, slots=True)
class DependencyVersion:
    name: str
    version: str

    def __post_init__(self) -> None:
        if not self.name.strip() or not self.version.strip():
            raise ValueError("dependency name and version cannot be empty")


@dataclass(frozen=True, slots=True)
class StrategyProvenance:
    kind: str
    component_id: str
    version: str
    schema_version: SchemaVersion = SchemaVersion("1.0.0")
    model: str | None = None
    model_version: str | None = None
    prompt_digest: CanonicalDigest | None = None
    template_version: str | None = None
    provider: str | None = None
    provider_version: str | None = None

    def __post_init__(self) -> None:
        if not self.kind.strip() or not self.component_id.strip() or not self.version.strip():
            raise ValueError("strategy kind, component ID, and version cannot be empty")


@dataclass(frozen=True, slots=True)
class ResourceUsage:
    runtime_seconds: float | None = None
    peak_memory_bytes: int | None = None
    peak_gpu_memory_bytes: int | None = None
    external_api_calls: int = 0
    external_api_tokens: int = 0
    estimated_cost: float | None = None

    def __post_init__(self) -> None:
        values = (self.runtime_seconds, self.estimated_cost)
        integers = (
            self.peak_memory_bytes,
            self.peak_gpu_memory_bytes,
            self.external_api_calls,
            self.external_api_tokens,
        )
        if any(value is not None and value < 0 for value in values):
            raise ValueError("resource usage cannot be negative")
        if any(value is not None and value < 0 for value in integers):
            raise ValueError("resource usage cannot be negative")


@dataclass(frozen=True, slots=True)
class ProvenanceRecord(VersionedSpec):
    code_commit: str = "unknown"
    dirty_tree: bool = False
    package_version: str = "unknown"
    python_version: str = "unknown"
    os: str = "unknown"
    architecture: str = "unknown"
    cpu: str = "unknown"
    gpu: str | None = None
    memory_bytes: int | None = None
    container_digests: tuple[CanonicalDigest, ...] = ()
    dependencies: tuple[DependencyVersion, ...] = ()
    schema_versions: Metadata = field(default_factory=dict)
    evidence_digests: Metadata = field(default_factory=dict)
    label_source_versions: Metadata = field(default_factory=dict)
    strategies: tuple[StrategyProvenance, ...] = ()
    seed: int | None = None
    deterministic_settings: Metadata = field(default_factory=dict)
    usage: ResourceUsage = field(default_factory=ResourceUsage)

    def __post_init__(self) -> None:
        if self.memory_bytes is not None and self.memory_bytes < 0:
            raise ValueError("memory size cannot be negative")
        object.__setattr__(
            self, "dependencies", tuple(sorted(self.dependencies, key=lambda x: x.name))
        )
        object.__setattr__(self, "schema_versions", _mapping(self.schema_versions))
        object.__setattr__(self, "evidence_digests", _mapping(self.evidence_digests))
        object.__setattr__(self, "label_source_versions", _mapping(self.label_source_versions))
        object.__setattr__(self, "deterministic_settings", _mapping(self.deterministic_settings))


@dataclass(frozen=True, slots=True)
class ArtifactRecord:
    logical_path: str
    digest: CanonicalDigest
    media_type: str
    origin: ArtifactOrigin = ArtifactOrigin.COMPUTED
    source_run_id: RunId | None = None

    def __post_init__(self) -> None:
        path = self.logical_path.strip().replace("\\", "/")
        if not path or path.startswith("/") or ".." in path.split("/"):
            raise ValueError("artifact logical path must be relative and bounded")
        if not self.media_type.strip():
            raise ValueError("artifact media type cannot be empty")
        if self.origin is ArtifactOrigin.COMPUTED and self.source_run_id is not None:
            raise ValueError("computed artifacts cannot name a source run")
        if self.origin is not ArtifactOrigin.COMPUTED and self.source_run_id is None:
            raise ValueError("cached/reused artifacts require a source run")
        object.__setattr__(self, "logical_path", path)


@dataclass(frozen=True, slots=True)
class RunFailure:
    category: ErrorCategory
    message: str
    resumable: bool

    def __post_init__(self) -> None:
        message = redact_text(self.message.strip())
        if not message:
            raise ValueError("run failure message cannot be empty")
        object.__setattr__(self, "message", message)


@dataclass(frozen=True, slots=True)
class RunTransition:
    sequence: int
    from_state: RunState | None
    to_state: RunState
    at: datetime
    reason: str | None = None

    def __post_init__(self) -> None:
        if self.sequence < 1:
            raise ValueError("transition sequence must be positive")
        object.__setattr__(self, "at", normalize_utc(self.at))
        if self.reason is not None:
            object.__setattr__(self, "reason", redact_text(self.reason.strip()))


@dataclass(frozen=True, slots=True)
class ExperimentRun(VersionedSpec):
    run_id: RunId | None = None
    experiment_id: ExperimentId | None = None
    specification_digest: CanonicalDigest | None = None
    state: RunState = RunState.PLANNED
    created_at: datetime | None = None
    queued_at: datetime | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    retry_of_run_id: RunId | None = None
    superseded_by_run_id: RunId | None = None
    completed_stages: tuple[str, ...] = ()
    failure: RunFailure | None = None
    artifacts: tuple[ArtifactRecord, ...] = ()
    transitions: tuple[RunTransition, ...] = ()
    provenance: ProvenanceRecord | None = None

    def __post_init__(self) -> None:
        if self.run_id is None or self.experiment_id is None or self.specification_digest is None:
            raise ValueError("run requires run, experiment, and specification identities")
        if self.experiment_id.value != str(self.specification_digest):
            raise ValueError("experiment ID must equal specification digest")
        if self.created_at is None:
            raise ValueError("run requires created_at")
        for name in ("created_at", "queued_at", "started_at", "ended_at"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, normalize_utc(value))
        stages = tuple(item.strip() for item in self.completed_stages)
        if any(not item for item in stages) or len(set(stages)) != len(stages):
            raise ValueError("completed stages must be nonempty and unique")
        object.__setattr__(self, "completed_stages", stages)
        if self.state is RunState.FAILED and self.failure is None:
            raise ValueError("failed run requires failure detail")
        if self.state is not RunState.FAILED and self.failure is not None:
            raise ValueError("only failed runs may contain failure detail")
        if self.state is RunState.SUPERSEDED and self.superseded_by_run_id is None:
            raise ValueError("superseded run requires its replacement run ID")
        if self.retry_of_run_id == self.run_id or self.superseded_by_run_id == self.run_id:
            raise ValueError("run cannot retry or supersede itself")
        if not self.transitions or self.transitions[-1].to_state is not self.state:
            raise ValueError("run transition audit must end at current state")
        if tuple(item.sequence for item in self.transitions) != tuple(
            range(1, len(self.transitions) + 1)
        ):
            raise ValueError("run transition audit sequence is not contiguous")

    @property
    def resumable(self) -> bool:
        return self.state is RunState.FAILED and self.failure is not None and self.failure.resumable

    @classmethod
    def planned(
        cls,
        experiment_id: ExperimentId,
        specification_digest: CanonicalDigest,
        at: datetime,
        *,
        retry_of_run_id: RunId | None = None,
        run_id: RunId | None = None,
    ) -> ExperimentRun:
        created = normalize_utc(at)
        return cls(
            run_id=run_id or RunId(f"run-{uuid4()}"),
            experiment_id=experiment_id,
            specification_digest=specification_digest,
            created_at=created,
            retry_of_run_id=retry_of_run_id,
            transitions=(RunTransition(1, None, RunState.PLANNED, created),),
        )

    def transition(
        self,
        target: RunState,
        at: datetime,
        *,
        reason: str | None = None,
        failure: RunFailure | None = None,
        completed_stages: tuple[str, ...] | None = None,
        artifacts: tuple[ArtifactRecord, ...] | None = None,
        provenance: ProvenanceRecord | None = None,
        superseded_by_run_id: RunId | None = None,
    ) -> ExperimentRun:
        require_transition(self.state, target, RUN_TRANSITIONS)
        timestamp = normalize_utc(at)
        event = RunTransition(len(self.transitions) + 1, self.state, target, timestamp, reason)
        return replace(
            self,
            state=target,
            queued_at=timestamp if target is RunState.QUEUED else self.queued_at,
            started_at=timestamp if target is RunState.RUNNING else self.started_at,
            ended_at=(
                timestamp
                if target
                in {
                    RunState.COMPLETED,
                    RunState.FAILED,
                    RunState.CANCELLED,
                    RunState.SUPERSEDED,
                }
                else self.ended_at
            ),
            failure=failure,
            completed_stages=completed_stages or self.completed_stages,
            artifacts=artifacts or self.artifacts,
            transitions=(*self.transitions, event),
            provenance=provenance or self.provenance,
            superseded_by_run_id=superseded_by_run_id,
        )


@dataclass(frozen=True, slots=True)
class EvaluationResult(VersionedSpec):
    run_id: RunId | None = None
    evaluator_id: str = ""
    evaluator_version: str = ""
    metrics: MetricValues = field(default_factory=dict)
    ranked_entity_ids: tuple[EntityId, ...] = ()
    findings_digest: CanonicalDigest | None = None
    coverage: MetricValues = field(default_factory=dict)
    details: Metadata = field(default_factory=dict)

    def __post_init__(self) -> None:
        if (
            self.run_id is None
            or not self.evaluator_id.strip()
            or not self.evaluator_version.strip()
        ):
            raise ValueError("evaluation requires run and versioned evaluator identities")
        if len(set(self.ranked_entity_ids)) != len(self.ranked_entity_ids):
            raise ValueError("ranked entity IDs must be unique")
        object.__setattr__(self, "metrics", _metrics(self.metrics))
        object.__setattr__(self, "coverage", _metrics(self.coverage))
        object.__setattr__(self, "details", _mapping(self.details))

    @property
    def analytical_digest(self) -> CanonicalDigest:
        return canonical_digest(
            {
                "schema_version": self.schema_version,
                "evaluator_id": self.evaluator_id,
                "evaluator_version": self.evaluator_version,
                "metrics": self.metrics,
                "ranked_entity_ids": self.ranked_entity_ids,
                "findings_digest": self.findings_digest,
                "coverage": self.coverage,
                "details": self.details,
            }
        )


@dataclass(frozen=True, slots=True)
class RankMovement:
    entity_id: EntityId
    control_rank: int | None
    treatment_rank: int | None

    @property
    def delta(self) -> int | None:
        if self.control_rank is None or self.treatment_rank is None:
            return None
        return self.control_rank - self.treatment_rank


@dataclass(frozen=True, slots=True)
class ObservationReport(VersionedSpec):
    observation_id: str = ""
    experiment_id: ExperimentId | None = None
    control_run_id: RunId | None = None
    treatment_run_id: RunId | None = None
    metric_deltas: MetricValues = field(default_factory=dict)
    rank_movements: tuple[RankMovement, ...] = ()
    regressions: tuple[str, ...] = ()
    cost_deltas: MetricValues = field(default_factory=dict)
    outcome: HypothesisOutcome = HypothesisOutcome.INCONCLUSIVE
    rationale: tuple[str, ...] = ()
    human_interpretation: str | None = None

    def __post_init__(self) -> None:
        if (
            not self.observation_id.strip()
            or self.experiment_id is None
            or self.control_run_id is None
            or self.treatment_run_id is None
        ):
            raise ValueError("observation requires observation, experiment, and run identities")
        object.__setattr__(self, "metric_deltas", _metrics(self.metric_deltas))
        object.__setattr__(self, "cost_deltas", _metrics(self.cost_deltas))

    @property
    def deterministic_digest(self) -> CanonicalDigest:
        """Digest deterministic fields while explicitly excluding human interpretation."""

        return canonical_digest(
            {
                "schema_version": self.schema_version,
                "observation_id": self.observation_id,
                "experiment_id": self.experiment_id,
                "control_run_id": self.control_run_id,
                "treatment_run_id": self.treatment_run_id,
                "metric_deltas": self.metric_deltas,
                "rank_movements": self.rank_movements,
                "regressions": self.regressions,
                "cost_deltas": self.cost_deltas,
                "outcome": self.outcome,
                "rationale": self.rationale,
            }
        )


def ranked_findings_digest(findings: Sequence[RankedFinding]) -> CanonicalDigest:
    """Digest a complete ordered ranking for replay comparisons."""

    return canonical_digest(tuple(findings))
