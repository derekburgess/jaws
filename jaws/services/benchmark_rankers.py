"""Common bounded benchmark ranker registry and baseline implementations."""

from __future__ import annotations

import random
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from math import log1p
from typing import Generic, Protocol, TypeVar, cast

import numpy as np
from sklearn.cluster import DBSCAN  # type: ignore[import-untyped]
from sklearn.decomposition import PCA  # type: ignore[import-untyped]
from sklearn.ensemble import IsolationForest  # type: ignore[import-untyped]
from sklearn.preprocessing import StandardScaler  # type: ignore[import-untyped]

from jaws.domain import (
    BenchmarkCandidate,
    CaptureId,
    ComparisonFrame,
    EntityId,
    FindingId,
    OutlierStatus,
    RankedFinding,
    RankerMetadata,
    RankerSpec,
    ReferenceEligibility,
    ReferenceEntity,
    ReferenceResult,
    Score,
    ScoreDirection,
    canonical_digest,
)

from .feature_registry import ENDPOINT_FEATURE_REGISTRY_V1
from .legacy_ranking import (
    LegacyBehavioralRanker,
    LegacyRepresentationBuilder,
    score_without_feature,
)

PLUGIN_SCHEMA_VERSION = "1.0.0"


class BenchmarkRanker(Protocol):
    @property
    def metadata(self) -> RankerMetadata: ...

    def rank(
        self, candidates: Sequence[BenchmarkCandidate], specification: RankerSpec
    ) -> tuple[RankedFinding, ...]: ...

    def explanation_fidelity(self) -> float | None: ...


PluginT = TypeVar("PluginT")


@dataclass(frozen=True, slots=True)
class PluginMetadata:
    kind: str
    component_id: str
    version: str
    plugin_schema_version: str = PLUGIN_SCHEMA_VERSION
    input_schema_version: str = "1.0.0"
    output_schema_version: str = "1.0.0"

    def __post_init__(self) -> None:
        if not self.kind.strip() or not self.component_id.strip() or not self.version.strip():
            raise ValueError("plugin kind, component ID, and version cannot be empty")
        if self.plugin_schema_version != PLUGIN_SCHEMA_VERSION:
            raise ValueError("unsupported plugin metadata schema")
        if self.input_schema_version != "1.0.0" or self.output_schema_version != "1.0.0":
            raise ValueError("plugin input/output schema is incompatible")


@dataclass(frozen=True, slots=True)
class PluginRegistration(Generic[PluginT]):
    metadata: PluginMetadata
    factory: Callable[[], PluginT]


class PluginRegistry(Generic[PluginT]):
    """Versioned registry that exposes factories, never provider/database credentials."""

    def __init__(self, kind: str) -> None:
        self.kind = kind
        self._plugins: dict[tuple[str, str], PluginRegistration[PluginT]] = {}

    def register(self, registration: PluginRegistration[PluginT]) -> None:
        if registration.metadata.kind != self.kind:
            raise ValueError(f"plugin kind must be {self.kind}")
        key = (registration.metadata.component_id, registration.metadata.version)
        if key in self._plugins:
            raise ValueError(f"plugin already registered: {self.kind}:{key}")
        self._plugins[key] = registration

    def resolve(self, component_id: str, version: str) -> PluginT:
        registration = self._plugins.get((component_id, version))
        if registration is None:
            raise KeyError(f"unavailable plugin: {self.kind}:{component_id}@{version}")
        return registration.factory()

    def list(self) -> tuple[PluginMetadata, ...]:
        return tuple(
            registration.metadata
            for _, registration in sorted(self._plugins.items(), key=lambda item: item[0])
        )


@dataclass(frozen=True, slots=True)
class BenchmarkRegistries:
    representations: PluginRegistry[object]
    references: PluginRegistry[object]
    rankers: PluginRegistry[BenchmarkRanker]
    evaluators: PluginRegistry[object]
    renderers: PluginRegistry[object]


class _ScoringRanker:
    metadata: RankerMetadata

    def __init__(self) -> None:
        self._fidelity: float | None = None

    def scores(
        self, candidates: Sequence[BenchmarkCandidate], specification: RankerSpec
    ) -> tuple[float, ...]:
        raise NotImplementedError

    def rank(
        self, candidates: Sequence[BenchmarkCandidate], specification: RankerSpec
    ) -> tuple[RankedFinding, ...]:
        _validate_spec(self.metadata, candidates, specification)
        scores = self.scores(candidates, specification)
        if self.metadata.explanation_capability == "none":
            self._fidelity = None
        else:
            ablated = self.ablation_scores(candidates, specification)
            self._fidelity = sum(
                abs(original - changed) > 1e-12
                for original, changed in zip(scores, ablated, strict=True)
            ) / len(scores)
        return _ranked_findings(candidates, scores, specification, self.metadata.ranker_id)

    def explanation_fidelity(self) -> float | None:
        return self._fidelity

    def ablation_scores(
        self, candidates: Sequence[BenchmarkCandidate], specification: RankerSpec
    ) -> tuple[float, ...]:
        ablated: list[BenchmarkCandidate] = []
        for candidate in candidates:
            if self.metadata.requires_embedding and candidate.embedding:
                index = max(
                    range(len(candidate.embedding)),
                    key=lambda row: abs(candidate.embedding[row]),
                )
                embedding = tuple(
                    0.0 if row == index else value for row, value in enumerate(candidate.embedding)
                )
                ablated.append(replace(candidate, embedding=embedding))
            elif candidate.features:
                feature = max(candidate.features, key=lambda name: abs(candidate.features[name]))
                features = {**candidate.features, feature: 0.0}
                ablated.append(replace(candidate, features=features))
            else:
                ablated.append(candidate)
        return self.scores(ablated, specification)


class SeededRandomRanker(_ScoringRanker):
    metadata = RankerMetadata(
        ranker_id="seeded_random",
        deterministic=False,
        requires_seed=True,
        explanation_capability="none",
    )

    def scores(
        self, candidates: Sequence[BenchmarkCandidate], specification: RankerSpec
    ) -> tuple[float, ...]:
        if specification.seed is None:
            raise ValueError("seeded random ranker requires an explicit seed")
        generator = random.Random(specification.seed)
        by_identity = {
            candidate.entity_id: generator.random()
            for candidate in sorted(candidates, key=lambda item: item.entity_id.value)
        }
        return tuple(by_identity[item.entity_id] for item in candidates)


class FeatureSortRanker(_ScoringRanker):
    def __init__(self, ranker_id: str, features: tuple[str, ...]) -> None:
        super().__init__()
        self.features = features
        self.metadata = RankerMetadata(
            ranker_id=ranker_id,
            explanation_capability="feature",
            required_features=features,
        )

    def scores(
        self, candidates: Sequence[BenchmarkCandidate], specification: RankerSpec
    ) -> tuple[float, ...]:
        return tuple(
            sum(candidate.features[name] for name in self.features) for candidate in candidates
        )


class FirstSeenRanker(_ScoringRanker):
    metadata = RankerMetadata(ranker_id="first_seen", explanation_capability="score")

    def scores(
        self, candidates: Sequence[BenchmarkCandidate], specification: RankerSpec
    ) -> tuple[float, ...]:
        return tuple(1.0 if candidate.first_seen else 0.0 for candidate in candidates)

    def ablation_scores(
        self, candidates: Sequence[BenchmarkCandidate], specification: RankerSpec
    ) -> tuple[float, ...]:
        return self.scores(
            tuple(replace(candidate, first_seen=False) for candidate in candidates), specification
        )


class UploadDownloadRatioRanker(_ScoringRanker):
    metadata = RankerMetadata(
        ranker_id="upload_download_ratio",
        explanation_capability="feature",
        required_features=("bytes_out", "bytes_in"),
    )

    def scores(
        self, candidates: Sequence[BenchmarkCandidate], specification: RankerSpec
    ) -> tuple[float, ...]:
        offset = float(specification.parameters.get("denominator_offset", 1.0))
        if offset <= 0:
            raise ValueError("ratio denominator offset must be positive")
        return tuple(
            candidate.features["bytes_out"] / (candidate.features["bytes_in"] + offset)
            for candidate in candidates
        )


class PeerRobustDeviationRanker(_ScoringRanker):
    metadata = RankerMetadata(ranker_id="peer_robust_deviation", explanation_capability="feature")

    def scores(
        self, candidates: Sequence[BenchmarkCandidate], specification: RankerSpec
    ) -> tuple[float, ...]:
        names, matrix = _numeric_matrix(candidates, specification)
        transformed = np.log1p(matrix)
        center = np.median(transformed, axis=0)
        mad = np.median(np.abs(transformed - center), axis=0)
        scale = np.where(mad > 1e-9, 1.4826 * mad, np.std(transformed, axis=0))
        scale = np.where(scale > 1e-9, scale, 1.0)
        deviations = np.abs((transformed - center) / scale)
        top_k = min(int(specification.parameters.get("top_k", 3)), len(names))
        return tuple(float(np.linalg.norm(np.sort(row)[-top_k:])) for row in deviations)


class OwnHistoryChangeRanker(_ScoringRanker):
    metadata = RankerMetadata(ranker_id="own_history_change", explanation_capability="feature")

    def scores(
        self, candidates: Sequence[BenchmarkCandidate], specification: RankerSpec
    ) -> tuple[float, ...]:
        names = _selected_features(candidates, specification)
        values: list[float] = []
        for candidate in candidates:
            if any(name not in candidate.history for name in names):
                raise ValueError("own-history ranker requires aligned historical features")
            changes = tuple(
                abs(log1p(candidate.features[name]) - log1p(candidate.history[name]))
                for name in names
            )
            values.append(float(np.linalg.norm(changes)))
        return tuple(values)


class NumericCurrentRanker(_ScoringRanker):
    metadata = RankerMetadata(ranker_id="numeric_current", explanation_capability="feature")

    def scores(
        self, candidates: Sequence[BenchmarkCandidate], specification: RankerSpec
    ) -> tuple[float, ...]:
        _, matrix = _numeric_matrix(candidates, specification)
        transformed = np.log1p(matrix)
        standardized = StandardScaler().fit_transform(transformed)
        return tuple(float(np.linalg.norm(row)) for row in standardized)


class EmbeddingPcaDbscanRanker(_ScoringRanker):
    metadata = RankerMetadata(
        ranker_id="embedding_pca_dbscan",
        explanation_capability="model",
        requires_embedding=True,
    )

    def scores(
        self, candidates: Sequence[BenchmarkCandidate], specification: RankerSpec
    ) -> tuple[float, ...]:
        matrix = np.asarray([candidate.embedding for candidate in candidates], dtype=float)
        if matrix.ndim != 2 or matrix.shape[1] == 0:
            raise ValueError("embedding ranker requires aligned embedding vectors")
        standardized = StandardScaler().fit_transform(matrix)
        components = min(
            int(specification.parameters.get("components", 2)),
            len(candidates),
            matrix.shape[1],
        )
        reduced = PCA(n_components=components, random_state=specification.seed or 0).fit_transform(
            standardized
        )
        eps = float(specification.parameters.get("eps", 1.0))
        min_samples = int(specification.parameters.get("min_samples", 2))
        labels = DBSCAN(eps=eps, min_samples=min_samples).fit_predict(reduced)
        center = np.median(reduced, axis=0)
        distances = np.linalg.norm(reduced - center, axis=1)
        return tuple(
            float(distance + (1.0 if label == -1 else 0.0))
            for distance, label in zip(distances, labels, strict=True)
        )


class IsolationForestRanker(_ScoringRanker):
    metadata = RankerMetadata(
        ranker_id="isolation_forest",
        deterministic=False,
        requires_seed=True,
        explanation_capability="model",
    )

    def scores(
        self, candidates: Sequence[BenchmarkCandidate], specification: RankerSpec
    ) -> tuple[float, ...]:
        if specification.seed is None:
            raise ValueError("Isolation Forest requires an explicit seed")
        _, matrix = _numeric_matrix(candidates, specification)
        model = IsolationForest(
            n_estimators=int(specification.parameters.get("n_estimators", 100)),
            contamination=str(specification.parameters.get("contamination", "auto")),
            random_state=specification.seed,
            n_jobs=1,
        )
        scores = -model.fit(np.log1p(matrix)).score_samples(np.log1p(matrix))
        return tuple(float(value) for value in scores)


class Legacy20BenchmarkRanker:
    metadata = RankerMetadata(
        ranker_id="legacy_2_0",
        version="1",
        explanation_capability="feature",
        required_features=tuple(
            sorted(
                {
                    source
                    for feature in ENDPOINT_FEATURE_REGISTRY_V1.feature_set.features
                    for source in feature.numerator_fields + feature.denominator_fields
                }
            )
        ),
    )

    def __init__(self) -> None:
        self._fidelity: float | None = None

    def rank(
        self, candidates: Sequence[BenchmarkCandidate], specification: RankerSpec
    ) -> tuple[RankedFinding, ...]:
        _validate_spec(self.metadata, candidates, specification)
        entities = tuple(item.entity_id for item in candidates)
        rows = tuple(cast(Mapping[str, object], item.features) for item in candidates)
        capture_id = _capture_id(candidates)
        representation = LegacyRepresentationBuilder(ENDPOINT_FEATURE_REGISTRY_V1).build_numeric(
            entities, rows, capture_id=capture_id
        )
        transformed = np.asarray(representation.transformed)
        center = tuple(float(value) for value in np.median(transformed, axis=0))
        reference = ReferenceResult(
            strategy_id="peer",
            strategy_version="1",
            entity_ids=entities,
            feature_names=representation.feature_names,
            centers=tuple(center for _ in entities),
            frames=tuple(
                tuple(ComparisonFrame.PEER for _ in representation.feature_names) for _ in entities
            ),
            entities=tuple(
                ReferenceEntity(entity, ReferenceEligibility.PEER) for entity in entities
            ),
            population=entities,
        )
        result = LegacyBehavioralRanker(ENDPOINT_FEATURE_REGISTRY_V1).rank(
            entities, representation, reference, specification
        )
        fidelity_rows = []
        for item in result.findings:
            if not item.contributions:
                fidelity_rows.append(False)
                continue
            strongest = max(
                item.contributions, key=lambda contribution: contribution.weighted_deviation
            )
            ablated = score_without_feature(
                item,
                strongest.feature,
                top_k=int(specification.parameters.get("top_k", 3)),
            )
            fidelity_rows.append(abs(item.score - ablated) > 1e-12)
        self._fidelity = sum(fidelity_rows) / len(fidelity_rows)
        return tuple(
            RankedFinding(
                finding_id=_finding_id("legacy_2_0", item.entity_id),
                entity_id=item.entity_id,
                rank=item.rank,
                score=Score(item.score, ScoreDirection.HIGHER_IS_MORE_ANOMALOUS),
                outlier=(
                    OutlierStatus.OUTLIER if item.model_label == -1 else OutlierStatus.NOT_SCORED
                ),
                evidence=item.evidence,
            )
            for item in result.findings
        )

    def explanation_fidelity(self) -> float | None:
        return self._fidelity


class ExampleMaximumFeatureRanker(FeatureSortRanker):
    """Minimal third-party-style extension using only bounded candidate features."""

    def __init__(self) -> None:
        super().__init__("example_max_bytes_out", ("bytes_out",))


def default_benchmark_registries() -> BenchmarkRegistries:
    rankers: PluginRegistry[BenchmarkRanker] = PluginRegistry("ranker")
    factories: tuple[tuple[str, Callable[[], BenchmarkRanker]], ...] = (
        ("seeded_random", SeededRandomRanker),
        ("total_bytes", lambda: FeatureSortRanker("total_bytes", ("bytes_out", "bytes_in"))),
        ("bytes_out", lambda: FeatureSortRanker("bytes_out", ("bytes_out",))),
        ("bytes_in", lambda: FeatureSortRanker("bytes_in", ("bytes_in",))),
        ("first_seen", FirstSeenRanker),
        ("upload_download_ratio", UploadDownloadRatioRanker),
        ("peer_robust_deviation", PeerRobustDeviationRanker),
        ("own_history_change", OwnHistoryChangeRanker),
        ("numeric_current", NumericCurrentRanker),
        ("embedding_pca_dbscan", EmbeddingPcaDbscanRanker),
        ("legacy_2_0", Legacy20BenchmarkRanker),
        ("isolation_forest", IsolationForestRanker),
    )
    for ranker_id, factory in factories:
        rankers.register(PluginRegistration(PluginMetadata("ranker", ranker_id, "1"), factory))
    registries = BenchmarkRegistries(
        PluginRegistry("representation"),
        PluginRegistry("reference"),
        rankers,
        PluginRegistry("evaluator"),
        PluginRegistry("renderer"),
    )
    for registry, component_id in (
        (registries.representations, "numeric"),
        (registries.representations, "embedding"),
        (registries.references, "peer"),
        (registries.references, "historical"),
        (registries.evaluators, "reward_vector"),
        (registries.renderers, "json"),
        (registries.renderers, "markdown"),
        (registries.renderers, "html"),
    ):
        registry.register(
            PluginRegistration(PluginMetadata(registry.kind, component_id, "1"), lambda: object())
        )
    return registries


def _validate_spec(
    metadata: RankerMetadata,
    candidates: Sequence[BenchmarkCandidate],
    specification: RankerSpec,
) -> None:
    if specification.ranker_id != metadata.ranker_id or specification.version != metadata.version:
        raise ValueError("ranker specification does not match registered metadata")
    if not candidates:
        raise ValueError("ranker requires a nonempty bounded candidate population")
    if metadata.requires_seed and specification.seed is None:
        raise ValueError("ranker requires an explicit seed")
    for candidate in candidates:
        missing = set(metadata.required_features) - set(candidate.features)
        if missing:
            raise ValueError(
                f"candidate {candidate.entity_id} is missing features: {sorted(missing)}"
            )
        if metadata.requires_embedding and not candidate.embedding:
            raise ValueError(f"candidate {candidate.entity_id} is missing an embedding")


def _selected_features(
    candidates: Sequence[BenchmarkCandidate], specification: RankerSpec
) -> tuple[str, ...]:
    selected = specification.parameters.get("features")
    if selected is None:
        common = set(candidates[0].features)
        for candidate in candidates[1:]:
            common &= set(candidate.features)
        result = tuple(sorted(common))
    elif isinstance(selected, Sequence) and not isinstance(selected, (str, bytes)):
        result = tuple(str(item) for item in selected)
    else:
        raise ValueError("ranker features parameter must be an array")
    if not result or any(
        any(name not in candidate.features for name in result) for candidate in candidates
    ):
        raise ValueError("selected numeric features are unavailable")
    return result


def _numeric_matrix(
    candidates: Sequence[BenchmarkCandidate], specification: RankerSpec
) -> tuple[tuple[str, ...], np.ndarray]:
    names = _selected_features(candidates, specification)
    matrix = np.asarray(
        [[candidate.features[name] for name in names] for candidate in candidates], dtype=float
    )
    if not np.all(np.isfinite(matrix)) or np.any(matrix < 0):
        raise ValueError("ranker numeric matrix must be finite and nonnegative")
    return names, matrix


def _ranked_findings(
    candidates: Sequence[BenchmarkCandidate],
    scores: Sequence[float],
    specification: RankerSpec,
    ranker_id: str,
) -> tuple[RankedFinding, ...]:
    if len(scores) != len(candidates) or any(not np.isfinite(value) for value in scores):
        raise ValueError("ranker scores must align and be finite")
    multiplier = -1 if specification.direction is ScoreDirection.HIGHER_IS_MORE_ANOMALOUS else 1
    order = sorted(
        range(len(candidates)),
        key=lambda index: (multiplier * scores[index], candidates[index].entity_id.value),
    )
    return tuple(
        RankedFinding(
            finding_id=_finding_id(ranker_id, candidates[index].entity_id),
            entity_id=candidates[index].entity_id,
            rank=rank,
            score=Score(float(scores[index]), specification.direction),
            evidence=candidates[index].evidence,
        )
        for rank, index in enumerate(order, 1)
    )


def _finding_id(ranker_id: str, entity_id: EntityId) -> FindingId:
    return FindingId(f"finding-{canonical_digest((ranker_id, entity_id))}")


def _capture_id(candidates: Sequence[BenchmarkCandidate]) -> CaptureId | None:
    for candidate in candidates:
        for evidence in candidate.evidence:
            if evidence.capture_id is not None:
                return evidence.capture_id
    return None
