"""Immutable, versioned research specifications and evidence joins."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType
from typing import Any, Mapping

from .enums import EntityType, OutlierStatus, ReferenceKind, ScoreDirection
from .identifiers import (
    CanonicalDigest,
    CaptureId,
    EntityId,
    ExperimentId,
    FindingId,
    SchemaVersion,
)
from .serialization import canonical_digest
from .time import normalize_utc

Parameters = Mapping[str, Any]


def _immutable_mapping(value: Parameters) -> Parameters:
    return MappingProxyType({key: _immutable_value(item) for key, item in value.items()})


def _immutable_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return _immutable_mapping(value)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return tuple(_immutable_value(item) for item in value)
    return value


@dataclass(frozen=True, slots=True)
class VersionedSpec:
    schema_version: SchemaVersion = SchemaVersion("1.0.0")

    @property
    def digest(self) -> CanonicalDigest:
        return canonical_digest(self)


@dataclass(frozen=True, slots=True)
class ObservationWindow(VersionedSpec):
    capture_ids: tuple[CaptureId, ...] = ()
    started_at: datetime | None = None
    ended_at: datetime | None = None
    perspective: EntityId | None = None
    filters: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.capture_ids:
            raise ValueError("observation window requires at least one capture")
        if self.started_at is not None:
            object.__setattr__(self, "started_at", normalize_utc(self.started_at))
        if self.ended_at is not None:
            object.__setattr__(self, "ended_at", normalize_utc(self.ended_at))
        if self.started_at and self.ended_at and self.ended_at < self.started_at:
            raise ValueError("observation window ends before it starts")


@dataclass(frozen=True, slots=True)
class EntityDefinition(VersionedSpec):
    entity_type: EntityType = EntityType.ENDPOINT_IP
    version: str = "1"


@dataclass(frozen=True, slots=True)
class RepresentationSpec(VersionedSpec):
    representation_id: str = ""
    version: str = "1"
    parameters: Parameters = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.representation_id.strip():
            raise ValueError("representation_id cannot be empty")
        object.__setattr__(self, "parameters", _immutable_mapping(self.parameters))


@dataclass(frozen=True, slots=True)
class ReferenceSpec(VersionedSpec):
    kind: ReferenceKind = ReferenceKind.PEER
    version: str = "1"
    parameters: Parameters = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "parameters", _immutable_mapping(self.parameters))


@dataclass(frozen=True, slots=True)
class RankerSpec(VersionedSpec):
    ranker_id: str = ""
    version: str = "1"
    direction: ScoreDirection = ScoreDirection.HIGHER_IS_MORE_ANOMALOUS
    seed: int | None = None
    parameters: Parameters = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.ranker_id.strip():
            raise ValueError("ranker_id cannot be empty")
        object.__setattr__(self, "parameters", _immutable_mapping(self.parameters))


@dataclass(frozen=True, slots=True)
class ExperimentSpec(VersionedSpec):
    hypothesis: str = ""
    observation: ObservationWindow | None = None
    entity: EntityDefinition = field(default_factory=EntityDefinition)
    representation: RepresentationSpec | None = None
    reference: ReferenceSpec = field(default_factory=ReferenceSpec)
    ranker: RankerSpec | None = None

    def __post_init__(self) -> None:
        if not self.hypothesis.strip():
            raise ValueError("experiment hypothesis cannot be empty")
        if self.observation is None or self.representation is None or self.ranker is None:
            raise ValueError("experiment requires observation, representation, and ranker specs")

    @property
    def experiment_id(self) -> ExperimentId:
        return ExperimentId(str(self.digest))


@dataclass(frozen=True, slots=True)
class EvidencePointer(VersionedSpec):
    capture_id: CaptureId | None = None
    entity_id: EntityId | None = None
    artifact_digest: CanonicalDigest | None = None
    selector: str | None = None

    def __post_init__(self) -> None:
        if self.capture_id is None and self.artifact_digest is None:
            raise ValueError("evidence pointer requires capture or artifact identity")


@dataclass(frozen=True, slots=True)
class Score:
    value: float
    direction: ScoreDirection

    def __post_init__(self) -> None:
        if not float("-inf") < self.value < float("inf"):
            raise ValueError("score must be finite")


@dataclass(frozen=True, slots=True)
class RankedFinding(VersionedSpec):
    finding_id: FindingId | None = None
    entity_id: EntityId | None = None
    rank: int = 0
    score: Score | None = None
    outlier: OutlierStatus = OutlierStatus.NOT_SCORED
    evidence: tuple[EvidencePointer, ...] = ()

    def __post_init__(self) -> None:
        if self.finding_id is None or self.entity_id is None or self.score is None:
            raise ValueError("finding requires finding, entity, and score identities")
        if self.rank < 1:
            raise ValueError("rank must be a positive one-based integer")
