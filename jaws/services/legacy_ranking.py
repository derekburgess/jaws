"""Pure `legacy_2_0` behavioral ranker and optional PCA/DBSCAN labeler."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from math import sqrt

import numpy as np
from kneed import KneeLocator  # type: ignore[import-untyped]
from sklearn.cluster import DBSCAN  # type: ignore[import-untyped]
from sklearn.decomposition import PCA  # type: ignore[import-untyped]
from sklearn.neighbors import NearestNeighbors  # type: ignore[import-untyped]
from sklearn.preprocessing import StandardScaler  # type: ignore[import-untyped]

from jaws.domain import (
    BehavioralRank,
    ClusterDiagnostics,
    ComparisonFrame,
    EntityId,
    EpsilonRecommendation,
    EvidencePointer,
    FeatureContribution,
    LegacyRankingResult,
    RankerSpec,
    ReferenceResult,
    RepresentationArtifacts,
    canonical_digest,
)

from .feature_registry import NumericFeatureRegistry

LEGACY_RANKER_ID = "legacy_2_0"
LEGACY_RANKER_VERSION = "1"


@dataclass(frozen=True, slots=True)
class LegacyRankerParameters:
    top_k: int = 3
    low_direction_weight: float = 0.5
    low_direction_cap: float = 3.0
    scale_floor: float = 0.1
    saturation_cap: float = 3.0
    reason_threshold: float = 2.5
    minimum_timing_packets: int = 4
    low_signal_features: tuple[str, ...] = ("interval_cv",)
    saturating_features: tuple[str, ...] = ("interval_mean",)
    active_features: tuple[str, ...] = ()

    @classmethod
    def from_spec(cls, spec: RankerSpec) -> LegacyRankerParameters:
        if spec.ranker_id != LEGACY_RANKER_ID or spec.version != LEGACY_RANKER_VERSION:
            raise ValueError("legacy ranker requires ranker_id='legacy_2_0', version='1'")
        defaults = cls()
        values = {
            name: spec.parameters.get(name, getattr(defaults, name))
            for name in cls.__dataclass_fields__
        }
        result = cls(**values)
        if result.top_k < 1 or result.scale_floor <= 0 or result.reason_threshold < 0:
            raise ValueError("legacy ranker parameters are outside their valid range")
        return result


def robust_center_scale(matrix: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    if matrix.ndim != 2 or not np.all(np.isfinite(matrix)):
        raise ValueError("ranking matrix must be finite and two-dimensional")
    center = np.median(matrix, axis=0)
    mad = np.median(np.abs(matrix - center), axis=0)
    scale = 1.4826 * mad
    return center, np.where(scale > 1e-9, scale, matrix.std(axis=0))


def weighted_deviations(
    z: np.ndarray, feature_names: Sequence[str], parameters: LegacyRankerParameters
) -> np.ndarray:
    low_signal = np.array([name in parameters.low_signal_features for name in feature_names])
    capped_low = (z < 0) & ~low_signal
    weighted = np.abs(z)
    weighted[capped_low] = np.minimum(
        weighted[capped_low] * parameters.low_direction_weight,
        parameters.low_direction_cap,
    )
    saturating = np.array([name in parameters.saturating_features for name in feature_names])
    weighted[:, saturating] = np.minimum(weighted[:, saturating], parameters.saturation_cap)
    return np.asarray(weighted)


def deviation_scores(weighted: np.ndarray, top_k: int) -> np.ndarray:
    if weighted.ndim != 2 or not np.all(np.isfinite(weighted)):
        raise ValueError("weighted deviations must be a finite matrix")
    k = min(top_k, weighted.shape[1])
    top = np.sort(weighted, axis=1)[:, -k:]
    return np.asarray(np.sqrt(np.sum(top**2, axis=1)))


class LegacyBehavioralRanker:
    """Continuous ranker; clustering labels are optional input, never score input."""

    def __init__(self, registry: NumericFeatureRegistry) -> None:
        self.registry = registry

    def rank(
        self,
        entities: Sequence[EntityId],
        representation: RepresentationArtifacts,
        reference: ReferenceResult,
        spec: RankerSpec,
        *,
        labels: Sequence[int] | None = None,
    ) -> LegacyRankingResult:
        parameters = LegacyRankerParameters.from_spec(spec)
        entity_ids = tuple(entities)
        if entity_ids != representation.entity_ids or entity_ids != reference.entity_ids:
            raise ValueError("entity order differs across representation and reference")
        if representation.feature_names != reference.feature_names:
            raise ValueError("representation and reference feature names differ")
        if representation.feature_names != self.registry.feature_set.feature_names:
            raise ValueError("ranker received the wrong feature-set version")
        transformed = np.asarray(representation.transformed, dtype=float)
        raw = np.asarray(representation.raw, dtype=float)
        self.registry.validate_matrix(raw)
        self.registry.validate_matrix(transformed)
        centers = np.asarray(reference.centers, dtype=float)
        frames = np.asarray(reference.frames, dtype=object)

        peer_center, peer_scale = robust_center_scale(transformed)
        z = np.zeros_like(transformed)
        peer_usable = peer_scale > 1e-9
        z[:, peer_usable] = (
            transformed[:, peer_usable] - peer_center[peer_usable]
        ) / peer_scale[peer_usable]

        historical_rows = np.any(frames == ComparisonFrame.OWN_HISTORY, axis=1)
        if historical_rows.any():
            residual = transformed - centers
            residual_center, residual_scale = robust_center_scale(residual[historical_rows])
            residual_scale = np.maximum(residual_scale, parameters.scale_floor)
            historical_z = (residual - residual_center) / residual_scale
            historical_cells = frames == ComparisonFrame.OWN_HISTORY
            z[historical_cells] = historical_z[historical_cells]

        names = representation.feature_names
        if {"packets_out", "packets_in", "interval_mean", "interval_cv"} <= set(names):
            packets = raw[:, names.index("packets_out")] + raw[:, names.index("packets_in")]
            sparse = packets < parameters.minimum_timing_packets
            z[np.ix_(sparse, [names.index("interval_mean"), names.index("interval_cv")])] = 0.0

        weighted = weighted_deviations(z, representation.feature_names, parameters)
        if parameters.active_features:
            inactive = [
                index
                for index, name in enumerate(representation.feature_names)
                if name not in parameters.active_features
            ]
            weighted[:, inactive] = 0.0
        scores = deviation_scores(weighted, parameters.top_k)
        labels_tuple = tuple(labels) if labels is not None else (None,) * len(entity_ids)
        if len(labels_tuple) != len(entity_ids):
            raise ValueError("model labels do not align with ranked entities")
        units = {row.name: row.unit.value for row in self.registry.metadata}
        depth = {row.entity_id: row.baseline_depth for row in reference.entities}
        findings: list[BehavioralRank] = []
        order = sorted(range(len(entity_ids)), key=lambda index: (-scores[index], entity_ids[index].value))
        for rank, index in enumerate(order, 1):
            contributions = tuple(
                FeatureContribution(
                    feature=name,
                    raw_value=float(raw[index, column]),
                    unit=units[name],
                    standardized_deviation=float(z[index, column]),
                    weighted_deviation=float(weighted[index, column]),
                    comparison_frame=frames[index, column],
                    baseline=(
                        float(np.expm1(centers[index, column]))
                        if frames[index, column] == ComparisonFrame.OWN_HISTORY
                        else None
                    ),
                    baseline_depth=depth[entity_ids[index]],
                )
                for column, name in enumerate(representation.feature_names)
            )
            findings.append(
                BehavioralRank(
                    entity_id=entity_ids[index],
                    rank=rank,
                    score=float(scores[index]),
                    model_label=labels_tuple[index],
                    contributions=contributions,
                    evidence=(
                        EvidencePointer(
                            artifact_digest=canonical_digest(representation),
                            entity_id=entity_ids[index],
                            selector=f"representation.rows[{index}]",
                        ),
                    ),
                )
            )
        return LegacyRankingResult(
            ranker_id=LEGACY_RANKER_ID,
            ranker_version=LEGACY_RANKER_VERSION,
            seed=spec.seed if spec.seed is not None else 0,
            reference=reference,
            representation=representation,
            findings=tuple(findings),
        )


class KDistanceEpsilonStrategy:
    strategy_id = "k_distance_knee"
    version = "1"

    def recommend(
        self, matrix: np.ndarray, min_samples: int, *, override: float | None = None
    ) -> EpsilonRecommendation:
        if min_samples < 1 or len(matrix) < min_samples:
            raise ValueError("epsilon recommendation requires at least min_samples rows")
        neighbors = NearestNeighbors(n_neighbors=min_samples).fit(matrix)
        distances, _ = neighbors.kneighbors(matrix)
        ordered = np.sort(distances[:, min_samples - 1])
        if override is not None:
            return EpsilonRecommendation(float(override), "override", None, tuple(ordered))
        knee = KneeLocator(
            range(len(ordered)), ordered, curve="convex", direction="increasing"
        ).knee
        if knee is None:
            return EpsilonRecommendation(float(np.median(ordered)), "median", None, tuple(ordered))
        index = int(knee)
        return EpsilonRecommendation(float(ordered[index]), "knee", index, tuple(ordered))


class LegacyRepresentationBuilder:
    """Build retained numeric and optional embedding/PCA artifacts."""

    def __init__(self, registry: NumericFeatureRegistry) -> None:
        self.registry = registry

    def build_numeric(
        self, entities: Sequence[EntityId], rows: Sequence[Mapping[str, object]]
    ) -> RepresentationArtifacts:
        raw = self.registry.raw_matrix(rows)
        transformed = self.registry.transform(raw)
        return RepresentationArtifacts(
            feature_set_id=self.registry.feature_set.feature_set_id,
            feature_set_version=self.registry.feature_set.version,
            entity_ids=tuple(entities),
            feature_names=self.registry.feature_set.feature_names,
            raw=tuple(tuple(float(value) for value in row) for row in raw),
            transformed=tuple(tuple(float(value) for value in row) for row in transformed),
            matrix=tuple(tuple(float(value) for value in row) for row in transformed),
        )

    def blend_embeddings(
        self,
        numeric: RepresentationArtifacts,
        embeddings: Sequence[Sequence[float]],
        *,
        components: int,
        whiten: bool,
        feature_weight: float,
        seed: int,
    ) -> RepresentationArtifacts:
        vectors = np.asarray(embeddings, dtype=float)
        if vectors.ndim != 2 or len(vectors) != len(numeric.entity_ids):
            raise ValueError("embedding representation does not align with entities")
        if not np.all(np.isfinite(vectors)):
            raise ValueError("embedding representation contains non-finite values")
        pca = PCA(n_components=components, whiten=whiten, random_state=seed)
        text = pca.fit_transform(vectors)
        if feature_weight > 0:
            behavior = StandardScaler().fit_transform(np.asarray(numeric.transformed))
            matrix = np.hstack((text, feature_weight * behavior))
        else:
            matrix = text
        return RepresentationArtifacts(
            feature_set_id=numeric.feature_set_id,
            feature_set_version=numeric.feature_set_version,
            entity_ids=numeric.entity_ids,
            feature_names=numeric.feature_names,
            raw=numeric.raw,
            transformed=numeric.transformed,
            matrix=tuple(tuple(float(value) for value in row) for row in matrix),
            pca_components=components,
            whiten=whiten,
            explained_variance=tuple(float(value) for value in pca.explained_variance_ratio_),
            feature_weight=feature_weight,
        )


class DBSCANLabeler:
    """Model labeling kept separate from the continuous behavioral score."""

    def __init__(self, epsilon: KDistanceEpsilonStrategy | None = None) -> None:
        self.epsilon = epsilon or KDistanceEpsilonStrategy()

    def label(
        self,
        representation: RepresentationArtifacts,
        *,
        min_samples: int,
        eps: float | None = None,
    ) -> ClusterDiagnostics:
        matrix = np.asarray(representation.matrix, dtype=float)
        recommendation = self.epsilon.recommend(matrix, min_samples, override=eps)
        labels = DBSCAN(eps=recommendation.value, min_samples=min_samples).fit_predict(matrix)
        sizes = tuple(
            sorted(
                (int(value) for value in np.unique(labels[labels != -1], return_counts=True)[1]),
                reverse=True,
            )
        )
        return ClusterDiagnostics(tuple(int(value) for value in labels), sizes, recommendation, min_samples)


def score_without_feature(finding: BehavioralRank, feature: str, *, top_k: int) -> float:
    """Explanation-fidelity hook: exact score after removing one retained contribution."""
    values = sorted(
        (
            row.weighted_deviation
            for row in finding.contributions
            if row.feature != feature
        ),
        reverse=True,
    )[:top_k]
    return sqrt(sum(value**2 for value in values))
