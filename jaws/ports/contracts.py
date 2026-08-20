"""Dependency protocols for deterministic JAWS application services.

The protocols describe direction and ownership, not concrete provider or storage schemas.
Later milestones can bind their versioned records to the generic parameters without
changing service call sites or importing infrastructure into the deterministic core.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from typing import Protocol, TypeVar

from jaws.domain import (
    CanonicalDigest,
    CaptureId,
    EmbeddingBatch,
    EmbeddingInput,
    EmbeddingProviderSpec,
    EntityId,
    LegacyRankingResult,
    ObservationWindow,
    RankedFinding,
    RankerSpec,
    ReferenceResult,
    ReferenceSpec,
    RepresentationArtifacts,
)

EvidenceT = TypeVar("EvidenceT")
PacketT_co = TypeVar("PacketT_co", covariant=True)
EntityT_contra = TypeVar("EntityT_contra", contravariant=True)
EnrichmentT_co = TypeVar("EnrichmentT_co", covariant=True)
CandidateT_contra = TypeVar("CandidateT_contra", contravariant=True)
ObservationT_contra = TypeVar("ObservationT_contra", contravariant=True)
ReferenceT_co = TypeVar("ReferenceT_co", covariant=True)
LabelT_contra = TypeVar("LabelT_contra", contravariant=True)
EvaluationT_co = TypeVar("EvaluationT_co", covariant=True)


class EvidenceStore(Protocol[EvidenceT]):
    """Append and retrieve evidence without exposing a database query language."""

    def append(self, capture_id: CaptureId, records: Sequence[EvidenceT]) -> None: ...

    def read(self, window: ObservationWindow) -> tuple[EvidenceT, ...]: ...


class ArtifactStore(Protocol):
    """Byte storage behind a portable logical path, returning a content digest."""

    def put(self, path: str, content: bytes) -> CanonicalDigest: ...

    def get(self, path: str) -> bytes: ...


class PacketSource(Protocol[PacketT_co]):
    """A bounded source of packet records for one ingest operation."""

    def packets(self) -> Iterator[PacketT_co]: ...


class CancellationSignal(Protocol):
    """Cooperative cancellation checked between bounded units of service work."""

    def is_cancelled(self) -> bool: ...


class EnrichmentProvider(Protocol[EntityT_contra, EnrichmentT_co]):
    """Resolve provider-neutral enrichment for one entity."""

    def enrich(self, entity: EntityT_contra) -> EnrichmentT_co: ...


class WaitStrategy(Protocol):
    """Wait between bounded external requests without fixing a runtime clock."""

    def wait(self, seconds: float) -> None: ...


class EmbeddingProvider(Protocol):
    """Embed identified texts in order with exact execution provenance."""

    @property
    def spec(self) -> EmbeddingProviderSpec: ...

    def embed(self, inputs: Sequence[EmbeddingInput]) -> EmbeddingBatch: ...


class Ranker(Protocol[CandidateT_contra]):
    """Rank bounded candidates under an explicit immutable specification."""

    def rank(
        self, candidates: Sequence[CandidateT_contra], spec: RankerSpec
    ) -> tuple[RankedFinding, ...]: ...


class ComparisonRanker(Protocol):
    """Rank aligned typed entities, representations, and a declared reference."""

    def rank(
        self,
        entities: Sequence[EntityId],
        representation: RepresentationArtifacts,
        reference: ReferenceResult,
        spec: RankerSpec,
        *,
        labels: Sequence[int] | None = None,
    ) -> LegacyRankingResult: ...


class ReferenceBuilder(Protocol[ObservationT_contra, ReferenceT_co]):
    """Build a declared comparison reference from bounded observations."""

    def build(
        self, observations: Sequence[ObservationT_contra], spec: ReferenceSpec
    ) -> ReferenceT_co: ...


class Evaluator(Protocol[LabelT_contra, EvaluationT_co]):
    """Evaluate complete findings against explicit labels or scenario truth."""

    def evaluate(
        self, findings: Sequence[RankedFinding], labels: LabelT_contra
    ) -> EvaluationT_co: ...
