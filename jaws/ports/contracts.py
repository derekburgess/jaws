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
    ObservationWindow,
    RankedFinding,
    RankerSpec,
    ReferenceSpec,
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


class EnrichmentProvider(Protocol[EntityT_contra, EnrichmentT_co]):
    """Resolve provider-neutral enrichment for one entity."""

    def enrich(self, entity: EntityT_contra) -> EnrichmentT_co: ...


class EmbeddingProvider(Protocol):
    """Embed texts in input order; provider metadata belongs in later result records."""

    def embed(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]: ...


class Ranker(Protocol[CandidateT_contra]):
    """Rank bounded candidates under an explicit immutable specification."""

    def rank(
        self, candidates: Sequence[CandidateT_contra], spec: RankerSpec
    ) -> tuple[RankedFinding, ...]: ...


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
