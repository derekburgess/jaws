"""Provider-neutral embedding inputs, vectors, and provenance."""

from __future__ import annotations

import math
from dataclasses import dataclass

from .enums import EmbeddingNormalization
from .identifiers import CanonicalDigest
from .serialization import canonical_digest


def _text(value: str, name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"embedding {name} cannot be empty")
    return normalized


@dataclass(frozen=True, slots=True)
class EmbeddingInput:
    """One ordered profile text with a stable content identity."""

    profile_id: str
    text: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "profile_id", _text(self.profile_id, "profile ID"))
        if not self.text:
            raise ValueError("embedding input text cannot be empty")

    @property
    def text_digest(self) -> CanonicalDigest:
        return canonical_digest(self.text)


@dataclass(frozen=True, slots=True)
class EmbeddingProviderSpec:
    """Exact provider execution metadata declared for one embedding batch."""

    provider_id: str
    model_id: str
    model_revision: str
    revision_exact: bool
    dimensions: int | None
    normalization: EmbeddingNormalization
    batch_size: int
    device: str

    def __post_init__(self) -> None:
        for field_name in ("provider_id", "model_id", "model_revision", "device"):
            object.__setattr__(self, field_name, _text(getattr(self, field_name), field_name))
        if not isinstance(self.revision_exact, bool):
            raise ValueError("embedding revision_exact must be boolean")
        if self.dimensions is not None and self.dimensions < 1:
            raise ValueError("embedding dimensions must be positive when declared")
        if self.batch_size < 1:
            raise ValueError("embedding batch size must be positive")


@dataclass(frozen=True, slots=True)
class EmbeddingVector:
    """Provider output tagged with the input content identity it represents."""

    input_text_digest: CanonicalDigest
    values: tuple[float, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "values", tuple(self.values))


@dataclass(frozen=True, slots=True)
class EmbeddingUsage:
    """Remote/local execution usage suitable for later experiment provenance."""

    requests: int = 0
    input_tokens: int | None = None
    total_tokens: int | None = None
    cost_usd: float | None = None

    def __post_init__(self) -> None:
        if self.requests < 0:
            raise ValueError("embedding request count cannot be negative")
        for field_name in ("input_tokens", "total_tokens"):
            value = getattr(self, field_name)
            if value is not None and value < 0:
                raise ValueError(f"embedding {field_name} cannot be negative")
        if self.cost_usd is not None and (not math.isfinite(self.cost_usd) or self.cost_usd < 0):
            raise ValueError("embedding cost must be finite and nonnegative")


@dataclass(frozen=True, slots=True)
class EmbeddingBatch:
    """Ordered provider result before service-level structural validation."""

    provider: EmbeddingProviderSpec
    vectors: tuple[EmbeddingVector, ...]
    usage: EmbeddingUsage = EmbeddingUsage()

    def __post_init__(self) -> None:
        object.__setattr__(self, "vectors", tuple(self.vectors))


@dataclass(frozen=True, slots=True)
class ProfileEmbedding:
    """Validated lineage from stored profile identity to text identity and vector."""

    profile_id: str
    input_text_digest: CanonicalDigest
    values: tuple[float, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "profile_id", _text(self.profile_id, "profile ID"))
        values = tuple(self.values)
        if not values or any(not math.isfinite(value) for value in values):
            raise ValueError("profile embedding values must be nonempty and finite")
        object.__setattr__(self, "values", values)


@dataclass(frozen=True, slots=True)
class ProfileEmbeddingProvenance:
    """Provider execution and input identity stored with one embedding-backed profile."""

    provider: EmbeddingProviderSpec
    input_text_digest: CanonicalDigest

    def __post_init__(self) -> None:
        if self.provider.dimensions is None:
            raise ValueError("profile embedding provenance requires dimensions")
