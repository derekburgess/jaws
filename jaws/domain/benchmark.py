"""Versioned benchmark governance, candidate, reward, and report contracts."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from enum import StrEnum
from math import isfinite
from types import MappingProxyType
from typing import Any
from urllib.parse import urlsplit

from .enums import EntityType, ReferenceKind, ScoreDirection
from .identifiers import CanonicalDigest, CaptureId, DatasetId, EntityId, RunId
from .research import ResourceUsage
from .specifications import EvidencePointer, ExperimentSpec, VersionedSpec
from .time import normalize_utc

Values = Mapping[str, float]
Metadata = Mapping[str, Any]


def _immutable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _immutable(item) for key, item in value.items()})
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return tuple(_immutable(item) for item in value)
    return value


def _mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    return MappingProxyType({str(key): _immutable(item) for key, item in value.items()})


def _values(value: Values) -> Values:
    result = {str(key).strip(): float(item) for key, item in value.items()}
    if any(not key for key in result) or any(not isfinite(item) for item in result.values()):
        raise ValueError("benchmark values require nonempty names and finite numbers")
    return MappingProxyType(result)


def _sha256(value: CanonicalDigest, field_name: str) -> None:
    text = str(value)
    if len(text) != 64 or any(character not in "0123456789abcdef" for character in text):
        raise ValueError(f"{field_name} must be lowercase SHA-256 text")


class BenchmarkPartition(StrEnum):
    DEVELOPMENT = "development"
    VALIDATION = "validation"
    HELD_OUT = "held_out"


class RedistributionPolicy(StrEnum):
    PERMITTED = "permitted"
    METADATA_ONLY = "metadata_only"
    SOURCE_TERMS = "source_terms"


class ScenarioStatus(StrEnum):
    COMPLETED = "completed"
    MISSING = "missing"
    SKIPPED = "skipped"
    FAILED = "failed"
    ABSTAINED = "abstained"


@dataclass(frozen=True, slots=True)
class LabelRecord:
    entity_id: EntityId
    relevance: int
    family: str
    source_version: str

    def __post_init__(self) -> None:
        if self.relevance < 0 or self.relevance > 3:
            raise ValueError("label relevance must be between zero and three")
        if not self.family.strip() or not self.source_version.strip():
            raise ValueError("label family and source version cannot be empty")


@dataclass(frozen=True, slots=True)
class ScenarioManifest(VersionedSpec):
    scenario_id: str = ""
    family: str = ""
    partition: BenchmarkPartition = BenchmarkPartition.DEVELOPMENT
    capture_ids: tuple[CaptureId, ...] = ()
    evidence_digest: CanonicalDigest | None = None
    labels: tuple[LabelRecord, ...] = ()
    labels_withheld: bool = False
    synthetic: bool = False
    model_author: str | None = None
    expected_behavior: str = ""
    known_limitations: tuple[str, ...] = ()
    available: bool = True

    def __post_init__(self) -> None:
        if not self.scenario_id.strip() or not self.family.strip():
            raise ValueError("scenario ID and family cannot be empty")
        if not self.capture_ids:
            raise ValueError("scenario requires at least one capture identity")
        if self.available and self.evidence_digest is None:
            raise ValueError("available scenario requires a verified evidence digest")
        if self.evidence_digest is not None:
            _sha256(self.evidence_digest, "scenario evidence digest")
        if self.synthetic != (self.model_author is not None):
            raise ValueError("synthetic scenarios must name their model author")
        if not self.expected_behavior.strip():
            raise ValueError("scenario expected behavior cannot be empty")
        if self.labels_withheld and self.labels:
            raise ValueError("withheld-label views cannot expose labels")
        if not self.labels_withheld and not self.labels:
            raise ValueError("scenario manifest requires labels")

    def tuning_view(self) -> ScenarioManifest:
        if self.partition is not BenchmarkPartition.HELD_OUT:
            return self
        return replace(self, labels=(), labels_withheld=True)


@dataclass(frozen=True, slots=True)
class DatasetManifest(VersionedSpec):
    dataset_id: DatasetId | None = None
    manifest_version: str = "1"
    title: str = ""
    source_url: str = ""
    source_location: str = ""
    acquisition_date: date | None = None
    safety_review_date: date | None = None
    license_name: str = ""
    license_url: str = ""
    redistribution: RedistributionPolicy = RedistributionPolicy.METADATA_ONLY
    checksum: CanonicalDigest | None = None
    checksum_scope: str = "source artifact"
    capture_host: str = "unknown"
    started_at: datetime | None = None
    ended_at: datetime | None = None
    label_source_version: str = ""
    label_provenance: str = ""
    scenarios: tuple[ScenarioManifest, ...] = ()
    known_limitations: tuple[str, ...] = ()
    contains_malware_binaries: bool = False

    def __post_init__(self) -> None:
        if self.dataset_id is None or not self.manifest_version.strip() or not self.title.strip():
            raise ValueError("dataset requires dataset, manifest, and title identities")
        for field_name in ("source_url", "license_url"):
            value = getattr(self, field_name)
            if urlsplit(value).scheme not in {"http", "https"}:
                raise ValueError(f"{field_name} must be an HTTP(S) URL")
        if not self.source_location.strip() or self.safety_review_date is None:
            raise ValueError("dataset source location and safety review date are required")
        if not self.license_name.strip() or not self.label_source_version.strip():
            raise ValueError("dataset license and label-source version are required")
        if not self.label_provenance.strip() or not self.scenarios:
            raise ValueError("dataset label provenance and scenarios are required")
        if self.checksum is not None:
            _sha256(self.checksum, "dataset checksum")
        if any(item.available for item in self.scenarios) and (
            self.acquisition_date is None or self.checksum is None
        ):
            raise ValueError("available datasets require acquisition date and checksum")
        if not self.checksum_scope.strip():
            raise ValueError("dataset checksum scope cannot be empty")
        if self.started_at is not None:
            object.__setattr__(self, "started_at", normalize_utc(self.started_at))
        if self.ended_at is not None:
            object.__setattr__(self, "ended_at", normalize_utc(self.ended_at))
        if self.started_at and self.ended_at and self.ended_at < self.started_at:
            raise ValueError("dataset time bounds are reversed")
        scenario_ids = tuple(item.scenario_id for item in self.scenarios)
        if len(set(scenario_ids)) != len(scenario_ids):
            raise ValueError("dataset scenario IDs must be unique")
        if self.contains_malware_binaries:
            raise ValueError("benchmark datasets may not contain malware binaries")

    def tuning_view(self) -> DatasetManifest:
        return replace(self, scenarios=tuple(item.tuning_view() for item in self.scenarios))


@dataclass(frozen=True, slots=True)
class BenchmarkCandidate:
    entity_id: EntityId
    features: Values
    history: Values = field(default_factory=dict)
    embedding: tuple[float, ...] = ()
    first_seen: bool = False
    evidence: tuple[EvidencePointer, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "features", _values(self.features))
        object.__setattr__(self, "history", _values(self.history))
        embedding = tuple(float(value) for value in self.embedding)
        if any(not isfinite(value) for value in embedding):
            raise ValueError("candidate embedding must be finite")
        object.__setattr__(self, "embedding", embedding)


@dataclass(frozen=True, slots=True)
class ScenarioData:
    manifest: ScenarioManifest
    candidates: tuple[BenchmarkCandidate, ...]

    def __post_init__(self) -> None:
        if not self.candidates and self.manifest.available:
            raise ValueError("available scenario data requires candidates")
        identities = tuple(item.entity_id for item in self.candidates)
        if len(set(identities)) != len(identities):
            raise ValueError("scenario candidate IDs must be unique")


@dataclass(frozen=True, slots=True)
class RankerMetadata(VersionedSpec):
    ranker_id: str = ""
    version: str = "1"
    direction: ScoreDirection = ScoreDirection.HIGHER_IS_MORE_ANOMALOUS
    entity_types: tuple[EntityType, ...] = (EntityType.ENDPOINT_IP,)
    reference_kinds: tuple[ReferenceKind, ...] = (ReferenceKind.PEER,)
    deterministic: bool = True
    requires_seed: bool = False
    explanation_capability: str = "none"
    required_features: tuple[str, ...] = ()
    requires_embedding: bool = False

    def __post_init__(self) -> None:
        if not self.ranker_id.strip() or not self.version.strip():
            raise ValueError("ranker metadata requires ID and version")
        if self.requires_seed and self.deterministic:
            raise ValueError("seeded stochastic rankers cannot claim deterministic execution")
        if self.explanation_capability not in {"none", "score", "feature", "model"}:
            raise ValueError("unsupported explanation capability")


@dataclass(frozen=True, slots=True)
class MetricAtK:
    k: int
    value: float

    def __post_init__(self) -> None:
        if self.k < 1 or not isfinite(self.value):
            raise ValueError("metric cutoff and value are invalid")


@dataclass(frozen=True, slots=True)
class TargetRank:
    entity_id: EntityId
    rank: int
    percentile: float

    def __post_init__(self) -> None:
        if self.rank < 1 or not 0 <= self.percentile <= 1:
            raise ValueError("target rank percentile is invalid")


@dataclass(frozen=True, slots=True)
class StabilityMetrics:
    top_k_overlap: float
    rank_correlation: float
    parameter_sensitivity: float

    def __post_init__(self) -> None:
        if not 0 <= self.top_k_overlap <= 1:
            raise ValueError("top-k overlap must be between zero and one")
        if not -1 <= self.rank_correlation <= 1 or self.parameter_sensitivity < 0:
            raise ValueError("stability metrics are outside their valid ranges")


@dataclass(frozen=True, slots=True)
class CoverageMetrics:
    total: int
    completed: int
    missing: int = 0
    skipped: int = 0
    failed: int = 0
    abstained: int = 0

    def __post_init__(self) -> None:
        values = (
            self.total,
            self.completed,
            self.missing,
            self.skipped,
            self.failed,
            self.abstained,
        )
        if any(value < 0 for value in values) or sum(values[1:]) != self.total:
            raise ValueError("coverage counts must be nonnegative and sum to total")


@dataclass(frozen=True, slots=True)
class RewardVector(VersionedSpec):
    recall: tuple[MetricAtK, ...] = ()
    mean_reciprocal_rank: float = 0.0
    ndcg: tuple[MetricAtK, ...] = ()
    benign_burden: tuple[MetricAtK, ...] = ()
    benign_above_first_relevant: int = 0
    target_ranks: tuple[TargetRank, ...] = ()
    stability: StabilityMetrics | None = None
    false_positive_movement: Values = field(default_factory=dict)
    explanation_fidelity: float | None = None
    usage: ResourceUsage = field(default_factory=ResourceUsage)
    coverage: CoverageMetrics = field(default_factory=lambda: CoverageMetrics(1, 1))
    scalar_objective: float | None = None
    scalar_weights: Values = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isfinite(self.mean_reciprocal_rank):
            raise ValueError("mean reciprocal rank must be finite")
        if self.benign_above_first_relevant < 0:
            raise ValueError("benign burden above first relevant cannot be negative")
        if self.explanation_fidelity is not None and not 0 <= self.explanation_fidelity <= 1:
            raise ValueError("explanation fidelity must be between zero and one")
        weights = _values(self.scalar_weights)
        movement = _values(self.false_positive_movement)
        if (self.scalar_objective is None) != (not weights):
            raise ValueError("scalar objectives require declared nonempty weights")
        if self.scalar_objective is not None and not isfinite(self.scalar_objective):
            raise ValueError("scalar objective must be finite")
        object.__setattr__(self, "scalar_weights", weights)
        object.__setattr__(self, "false_positive_movement", movement)

    def flattened(self) -> Values:
        result: dict[str, float] = {
            "mean_reciprocal_rank": self.mean_reciprocal_rank,
            "coverage_completed": self.coverage.completed / max(self.coverage.total, 1),
            "coverage_failed": self.coverage.failed / max(self.coverage.total, 1),
            "benign_above_first_relevant": float(self.benign_above_first_relevant),
            "runtime_seconds": self.usage.runtime_seconds or 0.0,
            "peak_memory_bytes": float(self.usage.peak_memory_bytes or 0),
            "peak_gpu_memory_bytes": float(self.usage.peak_gpu_memory_bytes or 0),
            "external_api_calls": float(self.usage.external_api_calls),
            "external_api_tokens": float(self.usage.external_api_tokens),
            "estimated_cost": self.usage.estimated_cost or 0.0,
        }
        result.update({f"recall_at_{item.k}": item.value for item in self.recall})
        result.update({f"ndcg_at_{item.k}": item.value for item in self.ndcg})
        result.update({f"benign_burden_at_{item.k}": item.value for item in self.benign_burden})
        if self.explanation_fidelity is not None:
            result["explanation_fidelity"] = self.explanation_fidelity
        if self.stability is not None:
            result.update(
                {
                    "stability_top_k_overlap": self.stability.top_k_overlap,
                    "stability_rank_correlation": self.stability.rank_correlation,
                    "parameter_sensitivity": self.stability.parameter_sensitivity,
                }
            )
        result.update(
            {
                f"false_positive_movement.{family}": value
                for family, value in self.false_positive_movement.items()
            }
        )
        result.update(
            {
                f"target_percentile.{target.entity_id.value}": target.percentile
                for target in self.target_ranks
            }
        )
        if self.scalar_objective is not None:
            result["scalar_objective"] = self.scalar_objective
        return _values(result)


@dataclass(frozen=True, slots=True)
class ScenarioBenchmarkResult(VersionedSpec):
    scenario_id: str = ""
    scenario_family: str = ""
    dataset_id: DatasetId | None = None
    run_id: RunId | None = None
    artifact_digest: CanonicalDigest | None = None
    parameter_digest: CanonicalDigest | None = None
    ranker_id: str = ""
    representation_id: str = ""
    reference_kind: ReferenceKind = ReferenceKind.PEER
    seed: int = 0
    window: str = "full"
    status: ScenarioStatus = ScenarioStatus.COMPLETED
    ranking: tuple[EntityId, ...] = ()
    reward: RewardVector | None = None
    message: str | None = None

    def __post_init__(self) -> None:
        if (
            not self.scenario_id.strip()
            or not self.scenario_family.strip()
            or self.dataset_id is None
            or not self.ranker_id.strip()
            or not self.representation_id.strip()
            or not self.window.strip()
        ):
            raise ValueError("scenario benchmark result requires scenario and ranker IDs")
        if self.status is ScenarioStatus.COMPLETED:
            if (
                self.run_id is None
                or self.artifact_digest is None
                or self.parameter_digest is None
                or not self.ranking
                or self.reward is None
            ):
                raise ValueError(
                    "completed scenario result requires run, artifact digest, ranking, and reward"
                )
            _sha256(self.artifact_digest, "run artifact digest")
            _sha256(self.parameter_digest, "ranker parameter digest")
        elif self.reward is not None or self.ranking:
            raise ValueError("non-completed scenario result cannot publish analytical results")


@dataclass(frozen=True, slots=True)
class AggregateMetric:
    name: str
    value: float
    contributing_run_ids: tuple[RunId, ...]

    def __post_init__(self) -> None:
        if not self.name.strip() or not isfinite(self.value) or not self.contributing_run_ids:
            raise ValueError("aggregate metric requires a finite value and contributing runs")


@dataclass(frozen=True, slots=True)
class PairedDelta:
    control_id: str
    treatment_id: str
    metric: str
    mean_delta: float
    lower_95: float
    upper_95: float
    pairs: int


@dataclass(frozen=True, slots=True)
class BenchmarkReport(VersionedSpec):
    benchmark_id: str = ""
    results: tuple[ScenarioBenchmarkResult, ...] = ()
    aggregates: tuple[AggregateMetric, ...] = ()
    paired_deltas: tuple[PairedDelta, ...] = ()
    coverage: CoverageMetrics = field(default_factory=lambda: CoverageMetrics(1, 1))
    required_baselines: tuple[str, ...] = ()
    environment: Metadata = field(default_factory=dict)
    artifact_checksums: Metadata = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.benchmark_id.strip() or not self.results:
            raise ValueError("benchmark report requires identity and scenario results")
        present = {item.ranker_id for item in self.results}
        if not set(self.required_baselines) <= present:
            raise ValueError("benchmark report omitted a required simple baseline")
        object.__setattr__(self, "environment", _mapping(self.environment))
        object.__setattr__(self, "artifact_checksums", _mapping(self.artifact_checksums))


@dataclass(frozen=True, slots=True)
class BenchmarkPolicy(VersionedSpec):
    expected_failures: Metadata = field(default_factory=dict)
    accepted_regressions: Metadata = field(default_factory=dict)
    regression_budgets: Metadata = field(default_factory=dict)
    required_baselines: tuple[str, ...] = ("seeded_random", "total_bytes")
    tier_samples: Metadata = field(default_factory=dict)
    forbid_aggregate_recall_only_claims: bool = True

    def __post_init__(self) -> None:
        expected = _mapping(self.expected_failures)
        accepted = _mapping(self.accepted_regressions)
        if set(expected) & set(accepted):
            raise ValueError("expected failures and accepted regressions must be distinct")
        object.__setattr__(self, "expected_failures", expected)
        object.__setattr__(self, "accepted_regressions", accepted)
        object.__setattr__(self, "regression_budgets", _mapping(self.regression_budgets))
        object.__setattr__(self, "tier_samples", _mapping(self.tier_samples))


@dataclass(frozen=True, slots=True)
class BenchmarkMatrixSpec(VersionedSpec):
    experiment: ExperimentSpec | None = None
    dataset_ids: tuple[DatasetId, ...] = ()
    scenario_ids: tuple[str, ...] = ()
    entity_types: tuple[EntityType, ...] = (EntityType.ENDPOINT_IP,)
    representation_ids: tuple[str, ...] = ()
    reference_kinds: tuple[ReferenceKind, ...] = (ReferenceKind.PEER,)
    ranker_ids: tuple[str, ...] = ()
    parameter_sets: tuple[Metadata, ...] = ({},)
    seeds: tuple[int, ...] = (0,)
    windows: tuple[str, ...] = ("full",)

    def __post_init__(self) -> None:
        if self.experiment is None or not self.dataset_ids or not self.scenario_ids:
            raise ValueError("benchmark matrix requires an experiment, datasets, and scenarios")
        if not self.representation_ids or not self.ranker_ids:
            raise ValueError("benchmark matrix requires representations and rankers")
        if not self.parameter_sets or not self.seeds or not self.windows:
            raise ValueError("benchmark parameter, seed, and window axes cannot be empty")
        object.__setattr__(
            self, "parameter_sets", tuple(_mapping(item) for item in self.parameter_sets)
        )
