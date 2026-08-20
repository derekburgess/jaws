"""Typed comparison, ranking, explanation, and rendering artifacts."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite

from .enums import ComparisonFrame, ReferenceEligibility
from .identifiers import CanonicalDigest, CaptureId, EntityId
from .specifications import EvidencePointer


@dataclass(frozen=True, slots=True)
class ReferenceEntity:
    entity_id: EntityId
    eligibility: ReferenceEligibility
    baseline_depth: int = 0
    first_seen: bool | None = None

    def __post_init__(self) -> None:
        if self.baseline_depth < 0:
            raise ValueError("baseline depth cannot be negative")


@dataclass(frozen=True, slots=True)
class ReferenceResult:
    """Comparison population plus per-cell frame/center metadata."""

    strategy_id: str
    strategy_version: str
    entity_ids: tuple[EntityId, ...]
    feature_names: tuple[str, ...]
    centers: tuple[tuple[float, ...], ...]
    frames: tuple[tuple[ComparisonFrame, ...], ...]
    entities: tuple[ReferenceEntity, ...]
    population: tuple[EntityId, ...]
    excluded: tuple[EntityId, ...] = ()

    def __post_init__(self) -> None:
        rows = len(self.entity_ids)
        columns = len(self.feature_names)
        if not self.strategy_id.strip() or not self.strategy_version.strip():
            raise ValueError("reference strategy identity cannot be empty")
        if len(set(self.entity_ids)) != rows or len(set(self.feature_names)) != columns:
            raise ValueError("reference entity and feature identities must be unique")
        if len(self.centers) != rows or any(len(row) != columns for row in self.centers):
            raise ValueError("reference centers do not match the representation")
        if len(self.frames) != rows or any(len(row) != columns for row in self.frames):
            raise ValueError("reference frames do not match the representation")
        if tuple(row.entity_id for row in self.entities) != self.entity_ids:
            raise ValueError("reference eligibility order must match entity order")
        if any(not isfinite(value) for row in self.centers for value in row):
            raise ValueError("reference centers must be finite")
        if set(self.population) & set(self.excluded):
            raise ValueError("excluded entities cannot belong to the comparison population")


@dataclass(frozen=True, slots=True)
class FeatureContribution:
    feature: str
    raw_value: float
    unit: str
    standardized_deviation: float
    weighted_deviation: float
    comparison_frame: ComparisonFrame
    baseline: float | None = None
    baseline_depth: int = 0

    def __post_init__(self) -> None:
        if not self.feature.strip() or not self.unit.strip():
            raise ValueError("contribution feature and unit cannot be empty")
        values = (self.raw_value, self.standardized_deviation, self.weighted_deviation)
        if not all(isfinite(value) for value in values):
            raise ValueError("contribution values must be finite")
        if self.baseline is not None and not isfinite(self.baseline):
            raise ValueError("contribution baseline must be finite")
        if self.baseline_depth < 0:
            raise ValueError("contribution baseline depth cannot be negative")


@dataclass(frozen=True, slots=True)
class BehavioralRank:
    entity_id: EntityId
    rank: int
    score: float
    model_label: int | None
    contributions: tuple[FeatureContribution, ...]
    evidence: tuple[EvidencePointer, ...]
    capture_id: CaptureId | None = None

    def __post_init__(self) -> None:
        if self.rank < 1 or not isfinite(self.score):
            raise ValueError("rank must be positive and score finite")
        if not self.evidence:
            raise ValueError("ranked findings require scoped evidence")


@dataclass(frozen=True, slots=True)
class EpsilonRecommendation:
    value: float
    source: str
    knee_index: int | None
    sorted_k_distances: tuple[float, ...]

    def __post_init__(self) -> None:
        if not isfinite(self.value) or self.value < 0:
            raise ValueError("epsilon must be finite and non-negative")
        if self.source not in {"knee", "median", "override", "manual"}:
            raise ValueError("unsupported epsilon source")


@dataclass(frozen=True, slots=True)
class ClusterDiagnostics:
    labels: tuple[int, ...]
    cluster_sizes: tuple[int, ...]
    epsilon: EpsilonRecommendation
    min_samples: int


@dataclass(frozen=True, slots=True)
class RepresentationArtifacts:
    feature_set_id: str
    feature_set_version: str
    entity_ids: tuple[EntityId, ...]
    feature_names: tuple[str, ...]
    raw: tuple[tuple[float, ...], ...]
    transformed: tuple[tuple[float, ...], ...]
    matrix: tuple[tuple[float, ...], ...]
    pca_components: int | None = None
    whiten: bool = False
    explained_variance: tuple[float, ...] = ()
    feature_weight: float = 1.0
    capture_id: CaptureId | None = None


@dataclass(frozen=True, slots=True)
class LegacyRankingResult:
    ranker_id: str
    ranker_version: str
    seed: int
    reference: ReferenceResult
    representation: RepresentationArtifacts
    findings: tuple[BehavioralRank, ...]
    clusters: ClusterDiagnostics | None = None


@dataclass(frozen=True, slots=True)
class PlotArtifactMetadata:
    kind: str
    input_digest: CanonicalDigest
    renderer_id: str
    renderer_version: str
    path: str | None = None
