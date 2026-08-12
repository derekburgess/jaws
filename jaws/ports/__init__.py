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
from .repositories import (
    CaptureNotFoundError,
    CaptureRepository,
    CaptureStateConflictError,
    DuplicateCaptureError,
    InactiveCaptureError,
    PacketRepository,
    RepositoryError,
    RepositorySchemaError,
)
from .repository_fakes import InMemoryCaptureRepository, InMemoryPacketRepository

__all__ = [
    "ArtifactStore",
    "CaptureNotFoundError",
    "CaptureRepository",
    "CaptureStateConflictError",
    "Clock",
    "EmbeddingProvider",
    "EnrichmentProvider",
    "Evaluator",
    "EvidenceStore",
    "DuplicateCaptureError",
    "FakeEmbeddingProvider",
    "FakeEnrichmentProvider",
    "FakeEvaluator",
    "FakeRanker",
    "FakeReferenceBuilder",
    "FrozenClock",
    "IdGenerator",
    "InMemoryArtifactStore",
    "InMemoryCaptureRepository",
    "InMemoryEvidenceStore",
    "InMemoryPacketRepository",
    "InactiveCaptureError",
    "PacketSource",
    "PacketRepository",
    "Ranker",
    "ReferenceBuilder",
    "RepositoryError",
    "RepositorySchemaError",
    "SequenceIdGenerator",
    "SequencePacketSource",
]
