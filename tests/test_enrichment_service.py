"""Pure provider-neutral enrichment classification and acquisition contracts."""

from dataclasses import dataclass, field
from datetime import UTC, datetime

import pytest

from jaws.domain import (
    EnrichmentObservation,
    EnrichmentStatus,
    EntityId,
    classify_ip_address,
)
from jaws.ports import (
    FakeEnrichmentProvider,
    FrozenClock,
    InMemoryEnrichmentRepository,
    RecordingWaitStrategy,
)
from jaws.services import EnrichmentAcquisitionPolicy, EnrichmentService

NOW = datetime(2026, 8, 20, 12, tzinfo=UTC)
NO_RETRY = EnrichmentAcquisitionPolicy(
    max_attempts=1,
    minimum_request_interval_seconds=0.0,
    initial_backoff_seconds=0.0,
    maximum_backoff_seconds=0.0,
)


@dataclass(slots=True)
class ScriptedEnrichmentProvider:
    responses: dict[EntityId, list[EnrichmentObservation]]
    requests: list[EntityId] = field(default_factory=list)

    def enrich(self, entity: EntityId) -> EnrichmentObservation:
        self.requests.append(entity)
        return self.responses[entity].pop(0)


class RecordingEnrichmentRepository(InMemoryEnrichmentRepository):
    def __init__(self, addresses):
        super().__init__(addresses=addresses)
        self.writes = []

    def put(self, record):
        self.writes.append(record)
        super().put(record)


def _service(repository, provider, policy=NO_RETRY, wait_strategy=None):
    return EnrichmentService(
        repository,
        provider,
        FrozenClock(NOW),
        policy,
        wait_strategy or RecordingWaitStrategy(),
    )


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


@pytest.mark.parametrize(
    "values",
    (
        {"max_attempts": 0},
        {"max_attempts": 11},
        {"minimum_request_interval_seconds": -1.0},
        {"initial_backoff_seconds": float("inf")},
        {"backoff_multiplier": 0.5},
        {"maximum_backoff_seconds": 301.0},
        {"initial_backoff_seconds": 2.0, "maximum_backoff_seconds": 1.0},
    ),
)
def test_acquisition_policy_rejects_unbounded_or_invalid_values(values):
    with pytest.raises(ValueError, match="enrichment"):
        EnrichmentAcquisitionPolicy(**values)


def test_transient_retries_use_capped_backoff_and_persist_the_final_success():
    entity = EntityId("ip:8.8.8.8")
    repository = RecordingEnrichmentRepository(addresses=("8.8.8.8",))
    provider = ScriptedEnrichmentProvider(
        {
            entity: [
                _observation(
                    EnrichmentStatus.TRANSIENT_FAILURE,
                    failure_code="fixture_unavailable",
                ),
                _observation(
                    EnrichmentStatus.TRANSIENT_FAILURE,
                    failure_code="fixture_rate_limited",
                ),
                _observation(
                    EnrichmentStatus.TRANSIENT_FAILURE,
                    failure_code="fixture_rate_limited",
                ),
                _observation(EnrichmentStatus.SUCCEEDED, organization="Google LLC"),
            ]
        }
    )
    waits = RecordingWaitStrategy()
    policy = EnrichmentAcquisitionPolicy(
        max_attempts=4,
        minimum_request_interval_seconds=0.25,
        initial_backoff_seconds=1.0,
        backoff_multiplier=2.0,
        maximum_backoff_seconds=3.0,
    )

    result = _service(repository, provider, policy, waits).enrich_pending()

    assert provider.requests == [entity, entity, entity, entity]
    assert waits.delays == [1.0, 2.0, 3.0]
    assert result.provider_attempts == 4
    assert result.retries == 3
    assert result.scheduled_wait_seconds == 6.0
    assert result.succeeded == 1
    assert result.transient_failures == 0
    assert result.records[0] == repository.get(entity)
    assert [record.status for record in repository.writes] == [
        EnrichmentStatus.TRANSIENT_FAILURE,
        EnrichmentStatus.TRANSIENT_FAILURE,
        EnrichmentStatus.TRANSIENT_FAILURE,
        EnrichmentStatus.SUCCEEDED,
    ]


def test_rate_pacing_applies_between_addresses_without_retrying_terminal_outcomes():
    first = EntityId("ip:1.1.1.1")
    second = EntityId("ip:8.8.8.8")
    repository = InMemoryEnrichmentRepository(addresses=("1.1.1.1", "8.8.8.8"))
    provider = FakeEnrichmentProvider(
        {
            first: _observation(EnrichmentStatus.NOT_FOUND, failure_code="fixture_missing"),
            second: _observation(
                EnrichmentStatus.PERMANENT_FAILURE,
                failure_code="fixture_rejected",
            ),
        }
    )
    waits = RecordingWaitStrategy()
    policy = EnrichmentAcquisitionPolicy(
        max_attempts=3,
        minimum_request_interval_seconds=0.5,
        initial_backoff_seconds=1.0,
        maximum_backoff_seconds=2.0,
    )

    result = _service(repository, provider, policy, waits).enrich_pending()

    assert provider.requests == [first, second]
    assert waits.delays == [0.5]
    assert result.provider_attempts == 2
    assert result.retries == 0
    assert result.not_found == 1
    assert result.permanent_failures == 1


def test_exhausted_transient_retry_remains_pending_for_a_later_run():
    entity = EntityId("ip:9.9.9.9")
    repository = InMemoryEnrichmentRepository(addresses=("9.9.9.9",))
    transient = _observation(
        EnrichmentStatus.TRANSIENT_FAILURE,
        failure_code="fixture_rate_limited",
    )
    provider = ScriptedEnrichmentProvider({entity: [transient, transient, transient]})
    waits = RecordingWaitStrategy()
    policy = EnrichmentAcquisitionPolicy(
        max_attempts=3,
        minimum_request_interval_seconds=0.1,
        initial_backoff_seconds=0.5,
        maximum_backoff_seconds=1.0,
    )

    result = _service(repository, provider, policy, waits).enrich_pending()

    assert waits.delays == [0.5, 1.0]
    assert result.provider_attempts == 3
    assert result.retries == 2
    assert result.transient_failures == 1
    assert repository.pending_addresses() == ("9.9.9.9",)


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
    service = _service(repository, provider)

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

    result = _service(repository, provider).enrich_pending()

    assert result.addresses_scanned == 0
    assert result.addresses_skipped_non_public == 2
    assert provider.requests == []


def test_public_provider_cannot_misclassify_an_address_as_not_applicable():
    repository = InMemoryEnrichmentRepository(addresses=("8.8.8.8",))
    provider = FakeEnrichmentProvider(
        {EntityId("ip:8.8.8.8"): _observation(EnrichmentStatus.NOT_APPLICABLE)}
    )

    with pytest.raises(ValueError, match="provider returned not-applicable"):
        _service(repository, provider).enrich_pending()
