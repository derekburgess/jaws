"""Deterministic in-memory implementations of the initial service ports."""

from __future__ import annotations

import hashlib
from collections.abc import Hashable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Generic, TypeVar

from jaws.domain import (
    CanonicalDigest,
    CaptureId,
    ObservationWindow,
    RankedFinding,
    RankerSpec,
    ReferenceSpec,
    normalize_utc,
)
from jaws.domain.identifiers import Identifier

RecordT = TypeVar("RecordT")
PacketT = TypeVar("PacketT")
EntityT = TypeVar("EntityT", bound=Hashable)
EnrichmentT = TypeVar("EnrichmentT")
CandidateT = TypeVar("CandidateT")
ObservationT = TypeVar("ObservationT")
ReferenceT = TypeVar("ReferenceT")
LabelT = TypeVar("LabelT")
EvaluationT = TypeVar("EvaluationT")
IdentifierT = TypeVar("IdentifierT", bound=Identifier)

if TYPE_CHECKING:
    from jaws.domain import Clock, IdGenerator

    from .contracts import (
        ArtifactStore,
        EmbeddingProvider,
        EnrichmentProvider,
        Evaluator,
        EvidenceStore,
        PacketSource,
        Ranker,
        ReferenceBuilder,
    )


@dataclass(slots=True)
class InMemoryEvidenceStore(Generic[RecordT]):
    """Capture-scoped append-only evidence preserving insertion order."""

    _records: dict[CaptureId, list[RecordT]] = field(default_factory=dict)

    def append(self, capture_id: CaptureId, records: Sequence[RecordT]) -> None:
        self._records.setdefault(capture_id, []).extend(records)

    def read(self, window: ObservationWindow) -> tuple[RecordT, ...]:
        return tuple(
            record
            for capture_id in window.capture_ids
            for record in self._records.get(capture_id, ())
        )


@dataclass(slots=True)
class InMemoryArtifactStore:
    """Logical-path byte storage returning the content SHA-256 digest."""

    _content: dict[str, bytes] = field(default_factory=dict)

    def put(self, path: str, content: bytes) -> CanonicalDigest:
        if not path:
            raise ValueError("artifact path cannot be empty")
        stored = bytes(content)
        self._content[path] = stored
        return CanonicalDigest(hashlib.sha256(stored).hexdigest())

    def get(self, path: str) -> bytes:
        return self._content[path]


@dataclass(frozen=True, slots=True)
class SequencePacketSource(Generic[PacketT]):
    """Replay a fixed packet sequence exactly once per iterator request."""

    values: tuple[PacketT, ...]

    def packets(self) -> Iterator[PacketT]:
        return iter(self.values)


@dataclass(slots=True)
class FakeEnrichmentProvider(Generic[EntityT, EnrichmentT]):
    responses: Mapping[EntityT, EnrichmentT]
    requests: list[EntityT] = field(default_factory=list)

    def enrich(self, entity: EntityT) -> EnrichmentT:
        self.requests.append(entity)
        return self.responses[entity]


@dataclass(slots=True)
class FakeEmbeddingProvider:
    responses: Mapping[str, tuple[float, ...]]
    requests: list[tuple[str, ...]] = field(default_factory=list)

    def embed(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        request = tuple(texts)
        self.requests.append(request)
        return tuple(self.responses[text] for text in request)


@dataclass(slots=True)
class FakeRanker(Generic[CandidateT]):
    findings: tuple[RankedFinding, ...]
    requests: list[tuple[tuple[CandidateT, ...], RankerSpec]] = field(default_factory=list)

    def rank(self, candidates: Sequence[CandidateT], spec: RankerSpec) -> tuple[RankedFinding, ...]:
        self.requests.append((tuple(candidates), spec))
        return self.findings


@dataclass(slots=True)
class FakeReferenceBuilder(Generic[ObservationT, ReferenceT]):
    reference: ReferenceT
    requests: list[tuple[tuple[ObservationT, ...], ReferenceSpec]] = field(default_factory=list)

    def build(self, observations: Sequence[ObservationT], spec: ReferenceSpec) -> ReferenceT:
        self.requests.append((tuple(observations), spec))
        return self.reference


@dataclass(slots=True)
class FakeEvaluator(Generic[LabelT, EvaluationT]):
    result: EvaluationT
    requests: list[tuple[tuple[RankedFinding, ...], LabelT]] = field(default_factory=list)

    def evaluate(self, findings: Sequence[RankedFinding], labels: LabelT) -> EvaluationT:
        self.requests.append((tuple(findings), labels))
        return self.result


@dataclass(frozen=True, slots=True)
class FrozenClock:
    value: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "value", normalize_utc(self.value))

    def now(self) -> datetime:
        return self.value


@dataclass(slots=True)
class SequenceIdGenerator(Generic[IdentifierT]):
    values: tuple[IdentifierT, ...]
    _position: int = 0

    def new(self) -> IdentifierT:
        if self._position >= len(self.values):
            raise IndexError("deterministic ID sequence exhausted")
        value = self.values[self._position]
        self._position += 1
        return value


if TYPE_CHECKING:

    def _assert_protocol_implementations(
        evidence: InMemoryEvidenceStore[str],
        artifacts: InMemoryArtifactStore,
        packets: SequencePacketSource[str],
        enrichment: FakeEnrichmentProvider[str, int],
        embeddings: FakeEmbeddingProvider,
        ranker: FakeRanker[str],
        references: FakeReferenceBuilder[str, int],
        evaluator: FakeEvaluator[str, int],
        clock: FrozenClock,
        ids: SequenceIdGenerator[Identifier],
    ) -> None:
        evidence_port: EvidenceStore[str] = evidence
        artifact_port: ArtifactStore = artifacts
        packet_port: PacketSource[str] = packets
        enrichment_port: EnrichmentProvider[str, int] = enrichment
        embedding_port: EmbeddingProvider = embeddings
        ranker_port: Ranker[str] = ranker
        reference_port: ReferenceBuilder[str, int] = references
        evaluator_port: Evaluator[str, int] = evaluator
        clock_port: Clock = clock
        id_port: IdGenerator[Identifier] = ids
        _ = (
            evidence_port,
            artifact_port,
            packet_port,
            enrichment_port,
            embedding_port,
            ranker_port,
            reference_port,
            evaluator_port,
            clock_port,
            id_port,
        )
