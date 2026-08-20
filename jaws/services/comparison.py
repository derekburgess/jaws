"""One headless comparison pipeline for endpoint and host-destination entities."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace

from jaws.domain import CaptureId, EntityId, LegacyRankingResult, RankerSpec, ReferenceSpec

from .feature_registry import NumericFeatureRegistry
from .legacy_ranking import (
    DBSCANLabeler,
    LegacyBehavioralRanker,
    LegacyRepresentationBuilder,
)
from .references import ReferenceObservation, TypedReferenceBuilder


@dataclass(frozen=True, slots=True)
class ComparisonRequest:
    entities: tuple[EntityId, ...]
    rows: tuple[Mapping[str, object], ...]
    histories: Mapping[EntityId, tuple[Mapping[str, object], ...]]
    reference: ReferenceSpec
    ranker: RankerSpec
    capture_id: str | None = None
    embeddings: tuple[tuple[float, ...], ...] = ()
    components: int = 2
    whiten: bool = False
    feature_weight: float = 1.0
    eps: float | None = None
    min_samples: int | None = None

    def __post_init__(self) -> None:
        if len(self.entities) != len(self.rows):
            raise ValueError("comparison entities and evidence rows must align")


class ComparisonService:
    """Coordinate pure representation, reference, rank, and optional label stages."""

    def __init__(self, registry: NumericFeatureRegistry) -> None:
        self.registry = registry
        self.representations = LegacyRepresentationBuilder(registry)
        self.references = TypedReferenceBuilder(registry)
        self.ranker = LegacyBehavioralRanker(registry)
        self.labeler = DBSCANLabeler()

    def compare(self, request: ComparisonRequest) -> LegacyRankingResult:
        observations = tuple(
            ReferenceObservation(
                entity_id=entity,
                values=row,
                history=request.histories.get(entity, ()),
                capture_id=request.capture_id,
            )
            for entity, row in zip(request.entities, request.rows, strict=True)
        )
        reference = self.references.build(observations, request.reference)
        selected = {entity: row for entity, row in zip(request.entities, request.rows, strict=True)}
        rows = tuple(selected[entity] for entity in reference.entity_ids)
        capture_id = (
            CaptureId(request.capture_id)
            if request.capture_id is not None and request.capture_id != "all"
            else None
        )
        numeric = self.representations.build_numeric(
            reference.entity_ids, rows, capture_id=capture_id
        )
        representation = numeric
        diagnostics = None
        labels = None
        if request.embeddings:
            embedded = {
                entity: vector
                for entity, vector in zip(request.entities, request.embeddings, strict=True)
            }
            representation = self.representations.blend_embeddings(
                numeric,
                tuple(embedded[entity] for entity in reference.entity_ids),
                components=request.components,
                whiten=request.whiten,
                feature_weight=request.feature_weight,
                seed=request.ranker.seed if request.ranker.seed is not None else 0,
            )
            min_samples = request.min_samples or 2 * request.components
            diagnostics = self.labeler.label(
                representation, min_samples=min_samples, eps=request.eps
            )
            labels = diagnostics.labels
        result = self.ranker.rank(
            reference.entity_ids, representation, reference, request.ranker, labels=labels
        )
        return replace(result, clusters=diagnostics)
