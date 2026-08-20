"""Validated embedding lineage, numeric-only operation, and atomic replacement."""

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from jaws.domain import (
    ENDPOINT_NUMERIC_FEATURE_SET_V1,
    ENDPOINT_TEXT_TEMPLATE_V1,
    EmbeddingBatch,
    EmbeddingInput,
    EmbeddingUsage,
    EndpointProfileDraft,
    EntityId,
    ObservationScopeId,
)
from jaws.ports import FakeEmbeddingProvider, FrozenClock, InMemoryProfileRepository
from jaws.services import EmbeddingValidationError, ProfileRepresentationService

NOW = datetime(2026, 8, 20, 12, tzinfo=UTC)
SCOPE = ObservationScopeId("scope_embedding_service")


def _draft(address: str, *, bytes_out: int) -> EndpointProfileDraft:
    return EndpointProfileDraft(
        entity_id=EntityId(f"ip:{address}"),
        ip_address=address,
        address_classification="private" if address.startswith("10.") else "public",
        bytes_out=bytes_out,
        packets_out=1,
        out_peers=1,
        out_ports=(443,),
        protocols=("TCP",),
    )


def _service(repository=None):
    return ProfileRepresentationService(
        repository=repository or InMemoryProfileRepository(),
        clock=FrozenClock(NOW),
    )


def test_embedding_service_retains_ordered_profile_text_vector_lineage_and_usage():
    repository = InMemoryProfileRepository()
    provider = FakeEmbeddingProvider(
        {
            "IP: 10.0.0.2 (private) | Organization: None | Hostname: None | Location: None\n"
            "Outbound: 20 bytes, 1 packets to 1 peers | Ports: [443]\n"
            "Inbound: 0 bytes, 0 packets from 0 peers | Ports: []\n"
            "Protocols: ['TCP']\n": (1.0, 0.0),
            "IP: 8.8.8.8 (public) | Organization: None | Hostname: None | Location: None\n"
            "Outbound: 10 bytes, 1 packets to 1 peers | Ports: [443]\n"
            "Inbound: 0 bytes, 0 packets from 0 peers | Ports: []\n"
            "Protocols: ['TCP']\n": (0.0, 1.0),
        },
        usage=EmbeddingUsage(requests=1, input_tokens=80, total_tokens=80, cost_usd=0.001),
    )

    result = _service(repository).replace_endpoint_scope(
        (_draft("8.8.8.8", bytes_out=10), _draft("10.0.0.2", bytes_out=20)),
        scope_id=SCOPE,
        legacy_scope="cap_embedding",
        numeric_feature_set=ENDPOINT_NUMERIC_FEATURE_SET_V1,
        text_template=ENDPOINT_TEXT_TEMPLATE_V1,
        embedding_provider=provider,
    )

    assert result.replaced_count == 2
    assert result.embedding_provider == provider.spec
    assert result.embedding_usage.input_tokens == 80
    assert tuple(profile.identity.entity_id.value for profile in result.profiles) == (
        "ip:10.0.0.2",
        "ip:8.8.8.8",
    )
    assert tuple(item.profile_id for item in result.embeddings) == tuple(
        profile.profile_key for profile in result.profiles
    )
    assert tuple(item.input_text_digest for item in result.embeddings) == tuple(
        request.text_digest for request in provider.requests[0]
    )
    assert tuple(profile.embedding for profile in repository.read_scope(SCOPE)) == (
        (1.0, 0.0),
        (0.0, 1.0),
    )
    assert all(profile.embedding_provenance is not None for profile in result.profiles)
    assert tuple(
        profile.embedding_provenance.input_text_digest  # type: ignore[union-attr]
        for profile in result.profiles
    ) == tuple(item.input_text_digest for item in result.embeddings)


def test_numeric_only_profiles_require_no_embedding_provider_or_optional_model_stack():
    repository = InMemoryProfileRepository()

    result = _service(repository).replace_endpoint_scope(
        (_draft("10.0.0.2", bytes_out=20),),
        scope_id=SCOPE,
        legacy_scope="cap_numeric",
        numeric_feature_set=ENDPOINT_NUMERIC_FEATURE_SET_V1,
    )

    profile = result.profiles[0]
    assert result.embedding_provider is None
    assert result.embeddings == ()
    assert profile.identity.representation_id == "endpoint_profile_numeric"
    assert profile.identity.model_id is None
    assert profile.embedding == ()
    assert repository.read_scope(SCOPE) == result.profiles


class _MalformedProvider:
    def __init__(self, base, mutation):
        self.spec = base.spec
        self._base = base
        self._mutation = mutation

    def embed(self, inputs: tuple[EmbeddingInput, ...]) -> EmbeddingBatch:
        batch = self._base.embed(inputs)
        if self._mutation == "count":
            return replace(batch, vectors=batch.vectors[:-1])
        if self._mutation == "order":
            return replace(batch, vectors=tuple(reversed(batch.vectors)))
        if self._mutation == "dimension":
            return replace(
                batch,
                vectors=(replace(batch.vectors[0], values=(1.0,)),) + batch.vectors[1:],
            )
        if self._mutation == "finite":
            return replace(
                batch,
                vectors=(replace(batch.vectors[0], values=(float("nan"), 0.0)),)
                + batch.vectors[1:],
            )
        if self._mutation == "provenance":
            return replace(
                batch,
                provider=replace(batch.provider, model_revision="different"),
            )
        raise AssertionError(self._mutation)


@pytest.mark.parametrize(
    ("mutation", "message"),
    (
        ("count", "count"),
        ("order", "order or input-text mapping"),
        ("dimension", "dimension"),
        ("finite", "finite"),
        ("provenance", "provenance changed"),
    ),
)
def test_malformed_provider_results_never_half_replace_a_profile_scope(mutation, message):
    repository = InMemoryProfileRepository()
    service = _service(repository)
    drafts = (_draft("10.0.0.2", bytes_out=20), _draft("8.8.8.8", bytes_out=10))
    service.replace_endpoint_scope(
        drafts,
        scope_id=SCOPE,
        legacy_scope="cap_existing",
        numeric_feature_set=ENDPOINT_NUMERIC_FEATURE_SET_V1,
    )
    existing = repository.read_scope(SCOPE)
    renderer = service.text_renderer
    responses = {
        renderer.render(draft, ENDPOINT_TEXT_TEMPLATE_V1): vector
        for draft, vector in zip(drafts, ((1.0, 0.0), (0.0, 1.0)), strict=True)
    }
    provider = _MalformedProvider(FakeEmbeddingProvider(responses), mutation)

    with pytest.raises(EmbeddingValidationError, match=message):
        service.replace_endpoint_scope(
            drafts,
            scope_id=SCOPE,
            legacy_scope="cap_replacement",
            numeric_feature_set=ENDPOINT_NUMERIC_FEATURE_SET_V1,
            text_template=ENDPOINT_TEXT_TEMPLATE_V1,
            embedding_provider=provider,
        )

    assert repository.read_scope(SCOPE) == existing


def test_provider_exceptions_leave_existing_scope_untouched():
    repository = InMemoryProfileRepository()
    service = _service(repository)
    drafts = (_draft("10.0.0.2", bytes_out=20),)
    service.replace_endpoint_scope(
        drafts,
        scope_id=SCOPE,
        legacy_scope="cap_existing",
        numeric_feature_set=ENDPOINT_NUMERIC_FEATURE_SET_V1,
    )
    existing = repository.read_scope(SCOPE)

    class FailingProvider:
        spec = FakeEmbeddingProvider({}).spec

        def embed(self, inputs):
            raise RuntimeError("provider unavailable")

    with pytest.raises(RuntimeError, match="provider unavailable"):
        service.replace_endpoint_scope(
            drafts,
            scope_id=SCOPE,
            legacy_scope="cap_replacement",
            numeric_feature_set=ENDPOINT_NUMERIC_FEATURE_SET_V1,
            text_template=ENDPOINT_TEXT_TEMPLATE_V1,
            embedding_provider=FailingProvider(),
        )

    assert repository.read_scope(SCOPE) == existing
