"""Minimal graph-discovery records for canonical ranked-finding artifacts."""

from __future__ import annotations

from dataclasses import dataclass

from .enums import OutlierStatus, ScoreDirection
from .identifiers import CanonicalDigest, EntityId, FindingId, RunId
from .serialization import canonical_digest


def _sha256(value: CanonicalDigest, field_name: str) -> None:
    text = str(value)
    if len(text) != 64 or any(character not in "0123456789abcdef" for character in text):
        raise ValueError(f"{field_name} must be lowercase SHA-256 text")


@dataclass(frozen=True, slots=True)
class FindingIndex:
    """One searchable ranking row without canonical result payloads."""

    finding_id: FindingId
    run_id: RunId
    entity_id: EntityId
    rank: int
    score: float
    score_direction: ScoreDirection
    outlier: OutlierStatus = OutlierStatus.NOT_SCORED

    def __post_init__(self) -> None:
        if self.rank < 1:
            raise ValueError("finding index rank must be a positive one-based integer")
        if not float("-inf") < self.score < float("inf"):
            raise ValueError("finding index score must be finite")


@dataclass(frozen=True, slots=True)
class FindingIndexBatch:
    """Complete atomic finding index publication for one canonical run artifact."""

    run_id: RunId
    artifact_digest: CanonicalDigest
    findings: tuple[FindingIndex, ...]

    def __post_init__(self) -> None:
        _sha256(self.artifact_digest, "artifact_digest")
        ordered = tuple(sorted(self.findings, key=lambda finding: finding.rank))
        if any(finding.run_id != self.run_id for finding in ordered):
            raise ValueError("every finding index row must belong to the published run")
        finding_ids = tuple(finding.finding_id for finding in ordered)
        if len(set(finding_ids)) != len(finding_ids):
            raise ValueError("finding index IDs must be unique within a run")
        entity_ids = tuple(finding.entity_id for finding in ordered)
        if len(set(entity_ids)) != len(entity_ids):
            raise ValueError("finding index entities must be unique within a run")
        if tuple(finding.rank for finding in ordered) != tuple(range(1, len(ordered) + 1)):
            raise ValueError("finding index ranks must be unique and contiguous from one")
        object.__setattr__(self, "findings", ordered)

    @property
    def digest(self) -> CanonicalDigest:
        return canonical_digest(self)
