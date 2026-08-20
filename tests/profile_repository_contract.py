"""Shared behavior contract for enrichment and profile repositories."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from jaws.domain import (
    CanonicalDigest,
    EmbeddingNormalization,
    EmbeddingProviderSpec,
    EndpointProfile,
    EnrichmentRecord,
    EnrichmentStatus,
    EntityId,
    ObservationScopeId,
    OutlierStatus,
    ProfileEmbeddingProvenance,
    ProfileIdentity,
    ResearcherAnnotation,
)
from jaws.ports import EntityNotFoundError, ProfileScopeConflictError


def _profile(
    address: str,
    scope_id: ObservationScopeId,
    legacy_scope: str,
    computed_at: datetime,
) -> EndpointProfile:
    return EndpointProfile(
        identity=ProfileIdentity(
            entity_id=EntityId(f"ip:{address}"),
            scope_id=scope_id,
            representation_id="endpoint-description",
            representation_version="1",
            model_id="fixture-model",
            model_revision="sha256:fixture",
        ),
        legacy_scope=legacy_scope,
        computed_at=computed_at,
        address_classification="public",
        organization="Example Networks",
        hostname="fixture.example",
        location="Example City",
        bytes_out=100,
        packets_out=2,
        out_peers=1,
        out_ports=(443,),
        bytes_in=200,
        packets_in=3,
        in_peers=1,
        in_ports=(50000,),
        protocols=("TCP",),
        interval_mean=1.5,
        interval_cv=0.1,
        embedding=(0.25, 0.75),
        embedding_provenance=ProfileEmbeddingProvenance(
            provider=EmbeddingProviderSpec(
                provider_id="fixture-provider",
                model_id="fixture-model",
                model_revision="sha256:fixture",
                revision_exact=True,
                dimensions=2,
                normalization=EmbeddingNormalization.L2,
                batch_size=16,
                device="test",
            ),
            input_text_digest=CanonicalDigest("fixture-input-digest"),
        ),
    )


def assert_enrichment_and_profile_repository_contract(repositories):
    acquired_at = datetime(2026, 8, 12, 12, tzinfo=UTC)
    first_entity = EntityId("ip:192.0.2.10")
    second_entity = EntityId("ip:198.51.100.20")

    assert repositories.enrichment.count_entities() == 2
    assert repositories.enrichment.legacy_unknown_addresses() == ("192.0.2.10",)
    assert repositories.enrichment.pending_addresses() == ("198.51.100.20",)
    assert repositories.enrichment.remove_legacy_unknown_ownership(("192.0.2.10",)) == 1
    assert repositories.enrichment.remove_legacy_unknown_ownership(("192.0.2.10",)) == 0
    assert repositories.enrichment.pending_addresses() == ("192.0.2.10", "198.51.100.20")

    enrichment = EnrichmentRecord(
        entity_id=first_entity,
        ip_address="192.0.2.10",
        status=EnrichmentStatus.SUCCEEDED,
        acquired_at=acquired_at,
        provider_id="fixture",
        provider_revision="1",
        organization="Example Networks",
        asn="AS64500",
        hostname="fixture.example",
        location="Example City",
        coordinates="1.0,2.0",
        confidence=0.9,
    )
    repositories.enrichment.put(enrichment)
    assert repositories.enrichment.get(first_entity) == enrichment
    metadata = {record.entity_id: record for record in repositories.enrichment.list_metadata()}
    assert metadata[first_entity].organization == "Example Networks"
    assert metadata[first_entity].hostname == "fixture.example"

    transient = EnrichmentRecord(
        entity_id=second_entity,
        ip_address="198.51.100.20",
        status=EnrichmentStatus.TRANSIENT_FAILURE,
        acquired_at=acquired_at,
        provider_id="fixture",
        provider_revision="1",
        failure_code="rate_limited",
    )
    repositories.enrichment.put(transient)
    assert repositories.enrichment.pending_addresses() == ("198.51.100.20",)
    with pytest.raises(EntityNotFoundError):
        repositories.enrichment.put(
            replace(enrichment, entity_id=EntityId("ip:203.0.113.1"), ip_address="203.0.113.1")
        )

    annotation = ResearcherAnnotation(
        entity_id=first_entity,
        key="role",
        value="control",
        author="researcher",
        recorded_at=acquired_at,
        ground_truth=True,
    )
    repositories.enrichment.add_annotation(annotation)
    assert repositories.enrichment.annotations(first_entity) == (annotation,)
    assert repositories.enrichment.get(first_entity) == enrichment

    first_scope = ObservationScopeId("scope_profile_fixture_first")
    second_scope = ObservationScopeId("scope_profile_fixture_second")
    first = _profile("192.0.2.10", first_scope, "cap_profile_fixture_first", acquired_at)
    second = _profile(
        "198.51.100.20",
        first_scope,
        "cap_profile_fixture_first",
        acquired_at,
    )
    latest = _profile(
        "192.0.2.10",
        second_scope,
        "cap_profile_fixture_second",
        acquired_at + timedelta(seconds=1),
    )

    assert repositories.profiles.replace_scope(first_scope, (second, first)) == 2
    assert repositories.profiles.read_scope(first_scope) == (first, second)
    with pytest.raises(ProfileScopeConflictError):
        repositories.profiles.replace_scope(first_scope, (first, latest))
    with pytest.raises(ProfileScopeConflictError):
        repositories.profiles.replace_scope(first_scope, (replace(first, computed_at=None),))
    assert repositories.profiles.read_scope(first_scope) == (first, second)

    assert repositories.profiles.replace_scope(second_scope, (latest,)) == 1
    assert repositories.profiles.read_history(first_scope) == ()
    assert repositories.profiles.read_history(second_scope) == (first, second)
    assert tuple(summary.scope_id for summary in repositories.profiles.list_scopes()) == (
        second_scope,
        first_scope,
    )
    assert (
        repositories.profiles.set_outliers(
            first_scope,
            {first_entity: OutlierStatus.OUTLIER, second_entity: OutlierStatus.INLIER},
        )
        == 2
    )
    verdicts = {
        record.identity.entity_id: record.outlier
        for record in repositories.profiles.read_scope(first_scope)
    }
    assert verdicts == {
        first_entity: OutlierStatus.OUTLIER,
        second_entity: OutlierStatus.INLIER,
    }
    with pytest.raises(EntityNotFoundError):
        repositories.profiles.set_outliers(
            first_scope, {EntityId("ip:203.0.113.1"): OutlierStatus.OUTLIER}
        )
    assert {
        record.identity.entity_id: record.outlier
        for record in repositories.profiles.read_scope(first_scope)
    } == verdicts

    first_summary = next(
        summary
        for summary in repositories.profiles.list_scopes()
        if summary.scope_id == first_scope
    )
    assert repositories.profiles.delete_scopes((first_summary,)) == 2
    assert repositories.profiles.read_scope(first_scope) == ()
    assert repositories.profiles.read_scope(second_scope) == (latest,)
