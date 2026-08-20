"""Pure provider-neutral enrichment classification and acquisition contracts."""

from datetime import UTC, datetime

import pytest

from jaws.domain import (
    EnrichmentObservation,
    EnrichmentStatus,
    EntityId,
    classify_ip_address,
)
from jaws.ports import FakeEnrichmentProvider, FrozenClock, InMemoryEnrichmentRepository
from jaws.services import EnrichmentService

NOW = datetime(2026, 8, 20, 12, tzinfo=UTC)


def _observation(status: EnrichmentStatus, **metadata) -> EnrichmentObservation:
    return EnrichmentObservation(
        status=status,
        provider_id="fixture-provider",
        provider_revision="2026-08-20",
        **metadata,
    )


def test_address_classification_is_provider_independent_and_ordered_by_scope():
    assert classify_ip_address("224.0.0.251") == "multicast"
    assert classify_ip_address("255.255.255.255") == "broadcast"
    assert classify_ip_address("0.0.0.0") == "unspecified"
    assert classify_ip_address("127.0.0.1") == "loopback"
    assert classify_ip_address("169.254.1.1") == "link-local"
    assert classify_ip_address("10.0.0.1") == "private"
    assert classify_ip_address("8.8.8.8") == "public"
    assert classify_ip_address("not-an-address") == "unknown"


def test_enrichment_observation_binds_normalized_entity_and_acquisition_time():
    observation = _observation(
        EnrichmentStatus.SUCCEEDED,
        organization=" Example Networks ",
        asn=" AS64500 ",
        confidence=0.75,
    )

    record = observation.for_address("2001:0db8::1", NOW)

    assert record.entity_id == EntityId("ip:2001:db8::1")
    assert record.ip_address == "2001:db8::1"
    assert record.organization == "Example Networks"
    assert record.asn == "AS64500"
    with pytest.raises(ValueError, match="requires resolved metadata"):
        _observation(EnrichmentStatus.SUCCEEDED)
    with pytest.raises(ValueError, match="cannot carry resolved metadata"):
        _observation(EnrichmentStatus.NOT_FOUND, organization="misleading")
    with pytest.raises(ValueError, match="cannot carry resolved metadata"):
        _observation(EnrichmentStatus.NOT_FOUND, confidence=0.5)


def test_service_persists_every_outcome_and_only_retries_transient_failures():
    repository = InMemoryEnrichmentRepository(
        addresses=(
            "10.0.0.1",
            "1.1.1.1",
            "8.8.8.8",
            "9.9.9.9",
            "208.67.222.222",
            "224.0.0.251",
        ),
        legacy_unknown_address_set={"10.0.0.1"},
    )
    provider = FakeEnrichmentProvider(
        {
            EntityId("ip:1.1.1.1"): _observation(
                EnrichmentStatus.NOT_FOUND, failure_code="fixture_not_found"
            ),
            EntityId("ip:8.8.8.8"): _observation(
                EnrichmentStatus.SUCCEEDED,
                organization="Google LLC",
                asn="AS15169",
            ),
            EntityId("ip:9.9.9.9"): _observation(
                EnrichmentStatus.TRANSIENT_FAILURE,
                failure_code="fixture_rate_limited",
            ),
            EntityId("ip:208.67.222.222"): _observation(
                EnrichmentStatus.PERMANENT_FAILURE,
                failure_code="fixture_rejected",
            ),
        }
    )
    observed = []
    service = EnrichmentService(repository, provider, FrozenClock(NOW))

    result = service.enrich_pending(observer=observed.append)

    assert result.total_addresses == 6
    assert result.addresses_scanned == 4
    assert result.addresses_skipped_non_public == 2
    assert result.addresses_already_documented == 0
    assert result.succeeded == result.organizations_added == 1
    assert result.not_found == 1
    assert result.transient_failures == 1
    assert result.permanent_failures == 1
    assert tuple(observed) == result.records
    assert repository.get(EntityId("ip:10.0.0.1")).status is EnrichmentStatus.NOT_APPLICABLE
    assert repository.get(EntityId("ip:10.0.0.1")).failure_code == "address_private"
    assert repository.get(EntityId("ip:224.0.0.251")).failure_code == "address_multicast"
    assert repository.legacy_unknown_addresses() == ()

    repeated = service.enrich_pending()

    assert repeated.addresses_scanned == 1
    assert repeated.addresses_skipped_non_public == 0
    assert repeated.addresses_already_documented == 5
    assert provider.requests.count(EntityId("ip:9.9.9.9")) == 2
    assert provider.requests.count(EntityId("ip:8.8.8.8")) == 1


def test_non_public_inventory_never_initializes_or_calls_remote_provider():
    repository = InMemoryEnrichmentRepository(addresses=("127.0.0.1", "10.0.0.1"))
    provider = FakeEnrichmentProvider({})

    result = EnrichmentService(repository, provider, FrozenClock(NOW)).enrich_pending()

    assert result.addresses_scanned == 0
    assert result.addresses_skipped_non_public == 2
    assert provider.requests == []


def test_public_provider_cannot_misclassify_an_address_as_not_applicable():
    repository = InMemoryEnrichmentRepository(addresses=("8.8.8.8",))
    provider = FakeEnrichmentProvider(
        {EntityId("ip:8.8.8.8"): _observation(EnrichmentStatus.NOT_APPLICABLE)}
    )

    with pytest.raises(ValueError, match="provider returned not-applicable"):
        EnrichmentService(repository, provider, FrozenClock(NOW)).enrich_pending()
