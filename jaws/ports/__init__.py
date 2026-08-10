"""Inward-facing dependency contracts and deterministic test implementations."""

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
from .fakes import (
    FakeEmbeddingProvider,
    FakeEnrichmentProvider,
    FakeEvaluator,
    FakeRanker,
    FakeReferenceBuilder,
    FrozenClock,
    InMemoryArtifactStore,
    InMemoryEvidenceStore,
    SequenceIdGenerator,
    SequencePacketSource,
)

__all__ = [
    "ArtifactStore",
    "Clock",
    "EmbeddingProvider",
    "EnrichmentProvider",
    "Evaluator",
    "EvidenceStore",
    "FakeEmbeddingProvider",
    "FakeEnrichmentProvider",
    "FakeEvaluator",
    "FakeRanker",
    "FakeReferenceBuilder",
    "FrozenClock",
    "IdGenerator",
    "InMemoryArtifactStore",
    "InMemoryEvidenceStore",
    "PacketSource",
    "Ranker",
    "ReferenceBuilder",
    "SequenceIdGenerator",
    "SequencePacketSource",
]
