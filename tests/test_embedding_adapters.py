"""Concrete provider adapters preserve order, provenance, batching, and usage."""

from types import SimpleNamespace

import pytest

from jaws.adapters import (
    EmbeddingProviderResponseError,
    LocalTransformerEmbeddingProvider,
    OpenAIEmbeddingProvider,
)
from jaws.domain import EmbeddingInput


class _OpenAIEndpoint:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.requests = []

    def create(self, *, input, model):
        self.requests.append((tuple(input), model))
        return next(self.responses)


def test_openai_adapter_batches_sorts_indexes_and_exposes_token_cost_metadata():
    endpoint = _OpenAIEndpoint(
        (
            SimpleNamespace(
                data=(
                    SimpleNamespace(index=1, embedding=(0.0, 1.0)),
                    SimpleNamespace(index=0, embedding=(1.0, 0.0)),
                ),
                usage=SimpleNamespace(prompt_tokens=10, total_tokens=10),
            ),
            SimpleNamespace(
                data=(SimpleNamespace(index=0, embedding=(0.5, 0.5)),),
                usage=SimpleNamespace(prompt_tokens=4, total_tokens=4),
            ),
        )
    )
    provider = OpenAIEmbeddingProvider(
        SimpleNamespace(embeddings=endpoint),
        "text-embedding-fixture",
        batch_size=2,
        cost_per_million_input_tokens=100.0,
    )
    inputs = tuple(EmbeddingInput(f"profile-{index}", text) for index, text in enumerate("abc"))

    result = provider.embed(inputs)

    assert endpoint.requests == [
        (("a", "b"), "text-embedding-fixture"),
        (("c",), "text-embedding-fixture"),
    ]
    assert tuple(vector.values for vector in result.vectors) == (
        (1.0, 0.0),
        (0.0, 1.0),
        (0.5, 0.5),
    )
    assert tuple(vector.input_text_digest for vector in result.vectors) == tuple(
        item.text_digest for item in inputs
    )
    assert result.provider.dimensions == 2
    assert result.provider.model_revision == "runtime-unpinned"
    assert result.provider.revision_exact is False
    assert result.usage.requests == 2
    assert result.usage.input_tokens == 14
    assert result.usage.total_tokens == 14
    assert result.usage.cost_usd == pytest.approx(0.0014)


def test_openai_adapter_rejects_duplicate_or_missing_response_indexes():
    endpoint = _OpenAIEndpoint(
        (
            SimpleNamespace(
                data=(
                    SimpleNamespace(index=0, embedding=(1.0, 0.0)),
                    SimpleNamespace(index=0, embedding=(0.0, 1.0)),
                ),
                usage=None,
            ),
        )
    )
    provider = OpenAIEmbeddingProvider(SimpleNamespace(embeddings=endpoint), "fixture")

    with pytest.raises(EmbeddingProviderResponseError, match="exactly once"):
        provider.embed((EmbeddingInput("one", "a"), EmbeddingInput("two", "b")))


def test_local_adapter_records_revision_device_batching_and_normalization():
    class Model:
        def __init__(self):
            self.requests = []

        def encode(self, sentences, *, batch_size, normalize_embeddings):
            self.requests.append((tuple(sentences), batch_size, normalize_embeddings))
            return [[1.0, 0.0], [0.0, 1.0]]

    model = Model()
    provider = LocalTransformerEmbeddingProvider(
        model,
        model_id="local-fixture",
        model_revision="sha256:abc123",
        revision_exact=True,
        device="cuda:0",
        batch_size=8,
    )
    inputs = (EmbeddingInput("one", "a"), EmbeddingInput("two", "b"))

    result = provider.embed(inputs)

    assert model.requests == [(("a", "b"), 8, True)]
    assert tuple(vector.values for vector in result.vectors) == ((1.0, 0.0), (0.0, 1.0))
    assert result.provider.provider_id == "sentence-transformers"
    assert result.provider.model_revision == "sha256:abc123"
    assert result.provider.revision_exact is True
    assert result.provider.dimensions == 2
    assert result.provider.device == "cuda:0"
