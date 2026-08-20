"""Validated endpoint representation construction and atomic scope replacement."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from jaws.domain import (
    ENDPOINT_NUMERIC_FEATURE_SET_V1,
    Clock,
    EmbeddingInput,
    EmbeddingProviderSpec,
    EmbeddingUsage,
    EmbeddingVector,
    EndpointProfile,
    EndpointProfileDraft,
    NumericFeatureSet,
    ObservationScopeId,
    ProfileEmbedding,
    ProfileEmbeddingProvenance,
    ProfileIdentity,
    TextTemplateSpec,
)
from jaws.ports import EmbeddingProvider, ProfileRepository

from .representations import EndpointTextRenderer


class EmbeddingValidationError(ValueError):
    """An embedding batch does not faithfully represent its ordered inputs."""


@dataclass(frozen=True, slots=True)
class ProfileRepresentationResult:
    scope_id: ObservationScopeId
    numeric_feature_set: NumericFeatureSet
    text_template: TextTemplateSpec | None
    embedding_provider: EmbeddingProviderSpec | None
    embedding_usage: EmbeddingUsage
    embeddings: tuple[ProfileEmbedding, ...]
    profiles: tuple[EndpointProfile, ...]
    replaced_count: int


@dataclass(frozen=True, slots=True)
class ProfileRepresentationService:
    repository: ProfileRepository
    clock: Clock
    text_renderer: EndpointTextRenderer = EndpointTextRenderer()

    def replace_endpoint_scope(
        self,
        drafts: Sequence[EndpointProfileDraft],
        *,
        scope_id: ObservationScopeId,
        legacy_scope: str,
        numeric_feature_set: NumericFeatureSet,
        text_template: TextTemplateSpec | None = None,
        embedding_provider: EmbeddingProvider | None = None,
    ) -> ProfileRepresentationResult:
        """Build every profile successfully before one atomic repository replacement."""

        if numeric_feature_set != ENDPOINT_NUMERIC_FEATURE_SET_V1:
            raise ValueError("endpoint representation requires endpoint numeric feature version 1")
        ordered = tuple(sorted(drafts, key=lambda draft: draft.entity_id.value))
        if len({draft.entity_id for draft in ordered}) != len(ordered):
            raise ValueError("endpoint representation drafts must have unique entity identities")
        if embedding_provider is None and text_template is not None:
            raise ValueError("text templates require an embedding provider")
        if embedding_provider is not None and text_template is None:
            raise ValueError("embedding-backed profiles require a text template")

        provider_spec: EmbeddingProviderSpec | None = None
        usage = EmbeddingUsage()
        validated_embeddings: tuple[ProfileEmbedding, ...] = ()
        identities: tuple[ProfileIdentity, ...]
        if embedding_provider is None:
            identities = tuple(
                ProfileIdentity(
                    entity_id=draft.entity_id,
                    scope_id=scope_id,
                    representation_id=numeric_feature_set.feature_set_id,
                    representation_version=numeric_feature_set.version,
                )
                for draft in ordered
            )
        else:
            assert text_template is not None
            self.text_renderer.validate_template(text_template)
            declared_provider = embedding_provider.spec
            identities = tuple(
                ProfileIdentity(
                    entity_id=draft.entity_id,
                    scope_id=scope_id,
                    representation_id=text_template.template_id,
                    representation_version=text_template.version,
                    model_id=declared_provider.model_id,
                    model_revision=declared_provider.model_revision,
                )
                for draft in ordered
            )
            inputs = tuple(
                EmbeddingInput(
                    identity.profile_key, self.text_renderer.render(draft, text_template)
                )
                for identity, draft in zip(identities, ordered, strict=True)
            )
            batch = embedding_provider.embed(inputs) if inputs else None
            if batch is not None:
                provider_spec = self._validate_provider(declared_provider, batch.provider)
                validated_embeddings = self._validate_vectors(inputs, batch.vectors, provider_spec)
                usage = batch.usage
            else:
                provider_spec = declared_provider

        vectors_by_profile = {embedding.profile_id: embedding for embedding in validated_embeddings}
        computed_at = self.clock.now()
        profiles = tuple(
            EndpointProfile(
                identity=identity,
                legacy_scope=legacy_scope,
                computed_at=computed_at,
                address_classification=draft.address_classification,
                organization=draft.organization,
                hostname=draft.hostname,
                location=draft.location,
                bytes_out=draft.bytes_out,
                packets_out=draft.packets_out,
                out_peers=draft.out_peers,
                out_ports=draft.out_ports,
                bytes_in=draft.bytes_in,
                packets_in=draft.packets_in,
                in_peers=draft.in_peers,
                in_ports=draft.in_ports,
                protocols=draft.protocols,
                interval_mean=draft.interval_mean,
                interval_cv=draft.interval_cv,
                embedding=(
                    vectors_by_profile[identity.profile_key].values
                    if identity.profile_key in vectors_by_profile
                    else ()
                ),
                embedding_provenance=(
                    ProfileEmbeddingProvenance(
                        provider=provider_spec,
                        input_text_digest=vectors_by_profile[
                            identity.profile_key
                        ].input_text_digest,
                    )
                    if provider_spec is not None and identity.profile_key in vectors_by_profile
                    else None
                ),
            )
            for identity, draft in zip(identities, ordered, strict=True)
        )
        replaced_count = self.repository.replace_scope(scope_id, profiles)
        return ProfileRepresentationResult(
            scope_id=scope_id,
            numeric_feature_set=numeric_feature_set,
            text_template=text_template,
            embedding_provider=provider_spec,
            embedding_usage=usage,
            embeddings=validated_embeddings,
            profiles=profiles,
            replaced_count=replaced_count,
        )

    @staticmethod
    def _validate_provider(
        declared: EmbeddingProviderSpec,
        returned: EmbeddingProviderSpec,
    ) -> EmbeddingProviderSpec:
        fields = (
            "provider_id",
            "model_id",
            "model_revision",
            "revision_exact",
            "normalization",
            "batch_size",
            "device",
        )
        if any(getattr(declared, field) != getattr(returned, field) for field in fields):
            raise EmbeddingValidationError("embedding provider provenance changed during request")
        if returned.dimensions is None:
            raise EmbeddingValidationError("embedding provider did not report dimensions")
        if declared.dimensions is not None and returned.dimensions != declared.dimensions:
            raise EmbeddingValidationError("embedding provider returned unexpected dimensions")
        return returned

    @staticmethod
    def _validate_vectors(
        inputs: Sequence[EmbeddingInput],
        vectors: Sequence[EmbeddingVector],
        provider: EmbeddingProviderSpec,
    ) -> tuple[ProfileEmbedding, ...]:
        if len(vectors) != len(inputs):
            raise EmbeddingValidationError("embedding count does not match input count")
        expected_digests = tuple(item.text_digest for item in inputs)
        returned_digests = tuple(vector.input_text_digest for vector in vectors)
        if returned_digests != expected_digests:
            raise EmbeddingValidationError("embedding order or input-text mapping changed")
        validated = []
        for item, vector in zip(inputs, vectors, strict=True):
            if len(vector.values) != provider.dimensions:
                raise EmbeddingValidationError("embedding vector dimension mismatch")
            if any(not math.isfinite(value) for value in vector.values):
                raise EmbeddingValidationError("embedding vectors must contain finite values")
            validated.append(
                ProfileEmbedding(item.profile_id, item.text_digest, tuple(vector.values))
            )
        return tuple(validated)
