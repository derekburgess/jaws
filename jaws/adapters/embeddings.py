"""Local-transformer and OpenAI embedding provider adapters."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Any, Protocol, cast

from jaws.domain import (
    EmbeddingBatch,
    EmbeddingInput,
    EmbeddingNormalization,
    EmbeddingProviderSpec,
    EmbeddingUsage,
    EmbeddingVector,
)


class EmbeddingProviderResponseError(RuntimeError):
    """A provider returned malformed output that cannot be mapped to its inputs."""


class _OpenAIEmbeddings(Protocol):
    def create(self, *, input: Sequence[str], model: str) -> object: ...


class _OpenAIClient(Protocol):
    embeddings: _OpenAIEmbeddings


@dataclass(frozen=True, slots=True)
class OpenAIEmbeddingProvider:
    client: object
    model_id: str
    model_revision: str = "runtime-unpinned"
    revision_exact: bool = False
    dimensions: int | None = None
    batch_size: int = 512
    cost_per_million_input_tokens: float | None = None

    @property
    def spec(self) -> EmbeddingProviderSpec:
        return EmbeddingProviderSpec(
            provider_id="openai",
            model_id=self.model_id,
            model_revision=self.model_revision,
            revision_exact=self.revision_exact,
            dimensions=self.dimensions,
            normalization=EmbeddingNormalization.L2,
            batch_size=self.batch_size,
            device="remote-api",
        )

    def embed(self, inputs: Sequence[EmbeddingInput]) -> EmbeddingBatch:
        request = tuple(inputs)
        vectors: list[EmbeddingVector] = []
        input_tokens = 0
        total_tokens = 0
        requests = 0
        client = cast(_OpenAIClient, self.client)
        for start in range(0, len(request), self.batch_size):
            chunk = request[start : start + self.batch_size]
            response = client.embeddings.create(
                input=[item.text for item in chunk],
                model=self.model_id,
            )
            requests += 1
            data = tuple(getattr(response, "data", ()))
            indexed = sorted(data, key=lambda item: int(getattr(item, "index")))
            if tuple(int(getattr(item, "index")) for item in indexed) != tuple(range(len(chunk))):
                raise EmbeddingProviderResponseError(
                    "OpenAI embedding indexes must cover every chunk input exactly once"
                )
            vectors.extend(
                EmbeddingVector(
                    chunk[int(getattr(item, "index"))].text_digest,
                    tuple(float(value) for value in getattr(item, "embedding")),
                )
                for item in indexed
            )
            usage = getattr(response, "usage", None)
            input_tokens += int(
                getattr(usage, "prompt_tokens", getattr(usage, "input_tokens", 0)) or 0
            )
            total_tokens += int(getattr(usage, "total_tokens", 0) or 0)

        dimensions = len(vectors[0].values) if vectors else self.dimensions
        provider = replace(self.spec, dimensions=dimensions)
        cost = (
            input_tokens * self.cost_per_million_input_tokens / 1_000_000
            if self.cost_per_million_input_tokens is not None
            else None
        )
        return EmbeddingBatch(
            provider=provider,
            vectors=tuple(vectors),
            usage=EmbeddingUsage(
                requests=requests,
                input_tokens=input_tokens,
                total_tokens=total_tokens,
                cost_usd=cost,
            ),
        )


class _LocalModel(Protocol):
    def encode(
        self,
        sentences: Sequence[str],
        *,
        batch_size: int,
        normalize_embeddings: bool,
    ) -> Any: ...


@dataclass(frozen=True, slots=True)
class LocalTransformerEmbeddingProvider:
    model: object
    model_id: str
    model_revision: str
    revision_exact: bool
    device: str
    dimensions: int | None = None
    batch_size: int = 32

    @property
    def spec(self) -> EmbeddingProviderSpec:
        return EmbeddingProviderSpec(
            provider_id="sentence-transformers",
            model_id=self.model_id,
            model_revision=self.model_revision,
            revision_exact=self.revision_exact,
            dimensions=self.dimensions,
            normalization=EmbeddingNormalization.L2,
            batch_size=self.batch_size,
            device=self.device,
        )

    def embed(self, inputs: Sequence[EmbeddingInput]) -> EmbeddingBatch:
        request = tuple(inputs)
        if not request:
            return EmbeddingBatch(provider=self.spec, vectors=())
        model = cast(_LocalModel, self.model)
        encoded = model.encode(
            [item.text for item in request],
            batch_size=self.batch_size,
            normalize_embeddings=True,
        )
        rows = encoded.tolist() if hasattr(encoded, "tolist") else encoded
        vectors = tuple(
            EmbeddingVector(item.text_digest, tuple(float(value) for value in row))
            for item, row in zip(request, rows, strict=True)
        )
        dimensions = len(vectors[0].values) if vectors else self.dimensions
        return EmbeddingBatch(
            provider=replace(self.spec, dimensions=dimensions),
            vectors=vectors,
            usage=EmbeddingUsage(requests=1),
        )
