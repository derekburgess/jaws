"""Pure peer, historical, hybrid, and researcher-defined reference strategies."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import numpy as np

from jaws.domain import (
    ComparisonFrame,
    EntityId,
    ReferenceEligibility,
    ReferenceEntity,
    ReferenceKind,
    ReferenceResult,
    ReferenceSpec,
)

from .feature_registry import NumericFeatureRegistry

POOLED_SCOPE = "all"


@dataclass(frozen=True, slots=True)
class ReferenceObservation:
    entity_id: EntityId
    values: Mapping[str, object]
    history: tuple[Mapping[str, object], ...] = ()
    capture_id: str | None = None
    tags: tuple[str, ...] = ()


class TypedReferenceBuilder:
    """Build comparison metadata without storage, plotting, or CLI knowledge."""

    def __init__(self, registry: NumericFeatureRegistry) -> None:
        self.registry = registry

    def build(
        self, observations: Sequence[ReferenceObservation], spec: ReferenceSpec
    ) -> ReferenceResult:
        rows = tuple(observations)
        if any(row.capture_id == POOLED_SCOPE for row in rows) and spec.kind in {
            ReferenceKind.HISTORICAL,
            ReferenceKind.HYBRID,
        }:
            raise ValueError("pooled scope cannot receive or supply a historical reference")

        selected, excluded = self._select(rows, spec)
        if not selected:
            raise ValueError("reference strategy selected an empty comparison population")
        entity_ids = tuple(row.entity_id for row in selected)
        raw = self.registry.raw_matrix([row.values for row in selected])
        transformed = self.registry.transform(raw)
        peer_centers = np.tile(np.median(transformed, axis=0), (len(selected), 1))
        centers = peer_centers.copy()
        frames = np.empty(centers.shape, dtype=object)
        frames[:] = ComparisonFrame.PEER
        min_sessions = int(spec.parameters.get("min_sessions", 2))
        exempt = frozenset(str(name) for name in spec.parameters.get("peer_features", ()))
        entities: list[ReferenceEntity] = []

        for index, row in enumerate(selected):
            depth = len(row.history)
            first_seen = depth == 0 if any(item.history for item in selected) else None
            eligible = depth >= min_sessions
            if spec.kind is ReferenceKind.PEER:
                status = ReferenceEligibility.PEER
            elif eligible:
                status = ReferenceEligibility.HISTORICAL
            elif depth == 0:
                status = ReferenceEligibility.FIRST_SEEN
            else:
                status = ReferenceEligibility.INSUFFICIENT_HISTORY

            if spec.kind in {ReferenceKind.HISTORICAL, ReferenceKind.HYBRID} and eligible:
                history_raw = self.registry.raw_matrix(row.history)
                medians = np.median(history_raw, axis=0)
                history_center = self.registry.transform(medians.reshape(1, -1))[0]
                for column, name in enumerate(self.registry.feature_set.feature_names):
                    if spec.kind is ReferenceKind.HYBRID and name in exempt:
                        continue
                    centers[index, column] = history_center[column]
                    frames[index, column] = ComparisonFrame.OWN_HISTORY
            entities.append(ReferenceEntity(row.entity_id, status, depth, first_seen))

        return ReferenceResult(
            strategy_id=spec.kind.value,
            strategy_version=spec.version,
            entity_ids=entity_ids,
            feature_names=self.registry.feature_set.feature_names,
            centers=tuple(tuple(float(value) for value in row) for row in centers),
            frames=tuple(tuple(value for value in row) for row in frames),
            entities=tuple(entities),
            population=entity_ids,
            excluded=tuple(row.entity_id for row in excluded),
        )

    @staticmethod
    def _select(
        rows: tuple[ReferenceObservation, ...], spec: ReferenceSpec
    ) -> tuple[tuple[ReferenceObservation, ...], tuple[ReferenceObservation, ...]]:
        if spec.kind is not ReferenceKind.RESEARCHER_DEFINED:
            return rows, ()
        captures = frozenset(str(value) for value in spec.parameters.get("capture_ids", ()))
        entities = frozenset(str(value) for value in spec.parameters.get("entity_ids", ()))
        tags = frozenset(str(value) for value in spec.parameters.get("tags", ()))

        def included(row: ReferenceObservation) -> bool:
            return (
                (not captures or row.capture_id in captures)
                and (not entities or row.entity_id.value in entities)
                and (not tags or bool(tags & set(row.tags)))
            )

        selected = tuple(row for row in rows if included(row))
        return selected, tuple(row for row in rows if not included(row))
