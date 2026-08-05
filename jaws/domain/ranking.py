"""Stable ranking rules shared by rankers and contract tests."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from .enums import ScoreDirection
from .identifiers import EntityId


@dataclass(frozen=True, slots=True)
class ScoredEntity:
    entity_id: EntityId
    score: float


def deterministic_ranking(
    values: Iterable[ScoredEntity], direction: ScoreDirection
) -> tuple[ScoredEntity, ...]:
    """Rank by score and then stable entity identity, independent of input order."""
    multiplier = -1 if direction is ScoreDirection.HIGHER_IS_MORE_ANOMALOUS else 1
    return tuple(sorted(values, key=lambda row: (multiplier * row.score, row.entity_id.value)))
