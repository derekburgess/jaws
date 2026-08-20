"""Deterministic provider-neutral enrichment acquisition and persistence."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from math import isfinite

from jaws.domain import (
    Clock,
    EnrichmentObservation,
    EnrichmentRecord,
    EnrichmentStatus,
    EntityId,
    classify_ip_address,
)
from jaws.ports import EnrichmentProvider, EnrichmentRepository, WaitStrategy

CLASSIFIER_PROVIDER_ID = "jaws-address-classifier"
CLASSIFIER_PROVIDER_REVISION = "1"

EnrichmentObserver = Callable[[EnrichmentRecord], None]


@dataclass(frozen=True, slots=True)
class EnrichmentAcquisitionPolicy:
    """Bound provider request pacing and transient retry delays."""

    max_attempts: int = 3
    minimum_request_interval_seconds: float = 0.2
    initial_backoff_seconds: float = 1.0
    backoff_multiplier: float = 2.0
    maximum_backoff_seconds: float = 8.0

    def __post_init__(self) -> None:
        if (
            isinstance(self.max_attempts, bool)
            or not isinstance(self.max_attempts, int)
            or not 1 <= self.max_attempts <= 10
        ):
            raise ValueError("enrichment max attempts must be an integer from 1 through 10")
        durations = (
            self.minimum_request_interval_seconds,
            self.initial_backoff_seconds,
            self.maximum_backoff_seconds,
        )
        if any(not isfinite(value) or value < 0.0 for value in durations):
            raise ValueError("enrichment wait durations must be finite and non-negative")
        if self.minimum_request_interval_seconds > 60.0:
            raise ValueError("enrichment request interval cannot exceed 60 seconds")
        if self.maximum_backoff_seconds > 300.0:
            raise ValueError("enrichment maximum backoff cannot exceed 300 seconds")
        if self.maximum_backoff_seconds < self.initial_backoff_seconds:
            raise ValueError("enrichment maximum backoff cannot be below its initial backoff")
        if not isfinite(self.backoff_multiplier) or self.backoff_multiplier < 1.0:
            raise ValueError("enrichment backoff multiplier must be finite and at least one")

    def retry_delay(self, failed_attempt: int) -> float:
        """Return the capped delay after a transient attempt numbered from one."""

        if not 1 <= failed_attempt < self.max_attempts:
            raise ValueError("failed attempt must precede a configured retry")
        delay = self.initial_backoff_seconds
        for _ in range(failed_attempt - 1):
            delay = min(delay * self.backoff_multiplier, self.maximum_backoff_seconds)
        return min(delay, self.maximum_backoff_seconds)


DEFAULT_ENRICHMENT_ACQUISITION_POLICY = EnrichmentAcquisitionPolicy()


def cleanup_legacy_unknown(repository: EnrichmentRepository) -> int:
    """Detach only non-public addresses from the legacy synthetic Unknown owner."""

    stale = tuple(
        address
        for address in repository.legacy_unknown_addresses()
        if classify_ip_address(address) != "public"
    )
    return repository.remove_legacy_unknown_ownership(stale)


@dataclass(frozen=True, slots=True)
class EnrichmentBatchResult:
    """Auditable outcome counts for one bounded pass over pending entities."""

    total_addresses: int
    addresses_scanned: int
    addresses_skipped_non_public: int
    addresses_already_documented: int
    succeeded: int
    not_found: int
    transient_failures: int
    permanent_failures: int
    provider_attempts: int
    retries: int
    scheduled_wait_seconds: float
    policy: EnrichmentAcquisitionPolicy
    records: tuple[EnrichmentRecord, ...]

    def __post_init__(self) -> None:
        counts = (
            self.total_addresses,
            self.addresses_scanned,
            self.addresses_skipped_non_public,
            self.addresses_already_documented,
            self.succeeded,
            self.not_found,
            self.transient_failures,
            self.permanent_failures,
            self.provider_attempts,
            self.retries,
        )
        if any(count < 0 for count in counts):
            raise ValueError("enrichment result counts cannot be negative")
        if (
            self.addresses_already_documented
            + self.addresses_scanned
            + self.addresses_skipped_non_public
            != self.total_addresses
        ):
            raise ValueError("enrichment result inventory counts do not balance")
        if (
            self.succeeded + self.not_found + self.transient_failures + self.permanent_failures
            != self.addresses_scanned
        ):
            raise ValueError("enrichment result provider outcomes do not balance")
        if len(self.records) != self.addresses_scanned + self.addresses_skipped_non_public:
            raise ValueError("enrichment result records do not match attempted inventory")
        if self.provider_attempts != self.addresses_scanned + self.retries:
            raise ValueError("enrichment result provider attempts do not balance")
        if self.retries > self.addresses_scanned * (self.policy.max_attempts - 1):
            raise ValueError("enrichment result retries exceed configured policy")
        if not isfinite(self.scheduled_wait_seconds) or self.scheduled_wait_seconds < 0.0:
            raise ValueError("enrichment result wait must be finite and non-negative")

    @property
    def organizations_added(self) -> int:
        """Compatibility count for successful provider observations this run."""

        return self.succeeded


@dataclass(frozen=True, slots=True)
class EnrichmentService:
    """Classify pending IPs, acquire public metadata, and persist every explicit outcome."""

    repository: EnrichmentRepository
    provider: EnrichmentProvider[EntityId, EnrichmentObservation]
    clock: Clock
    policy: EnrichmentAcquisitionPolicy
    wait_strategy: WaitStrategy

    def enrich_pending(
        self, *, observer: EnrichmentObserver | None = None
    ) -> EnrichmentBatchResult:
        cleanup_legacy_unknown(self.repository)

        total_addresses = self.repository.count_entities()
        pending = self.repository.pending_addresses()
        already_documented = total_addresses - len(pending)
        records: list[EnrichmentRecord] = []
        public_count = 0
        provider_attempts = 0
        scheduled_wait_seconds = 0.0

        for address in pending:
            classification = classify_ip_address(address)
            if classification == "public":
                public_count += 1
                entity = EntityId(f"ip:{address}")
                retry_delay = 0.0
                for attempt in range(1, self.policy.max_attempts + 1):
                    pacing_delay = (
                        self.policy.minimum_request_interval_seconds if provider_attempts else 0.0
                    )
                    delay = max(pacing_delay, retry_delay)
                    if delay:
                        self.wait_strategy.wait(delay)
                        scheduled_wait_seconds += delay
                    provider_attempts += 1
                    observation = self.provider.enrich(entity)
                    if observation.status is EnrichmentStatus.NOT_APPLICABLE:
                        raise ValueError("public enrichment provider returned not-applicable")
                    record = observation.for_address(address, self.clock.now())
                    self.repository.put(record)
                    if (
                        observation.status is not EnrichmentStatus.TRANSIENT_FAILURE
                        or attempt == self.policy.max_attempts
                    ):
                        break
                    retry_delay = self.policy.retry_delay(attempt)
            else:
                observation = EnrichmentObservation(
                    status=EnrichmentStatus.NOT_APPLICABLE,
                    provider_id=CLASSIFIER_PROVIDER_ID,
                    provider_revision=CLASSIFIER_PROVIDER_REVISION,
                    failure_code=f"address_{classification.replace('-', '_')}",
                )
                record = observation.for_address(address, self.clock.now())
                self.repository.put(record)
            records.append(record)
            if observer is not None:
                observer(record)

        statuses = tuple(record.status for record in records)
        return EnrichmentBatchResult(
            total_addresses=total_addresses,
            addresses_scanned=public_count,
            addresses_skipped_non_public=len(pending) - public_count,
            addresses_already_documented=already_documented,
            succeeded=statuses.count(EnrichmentStatus.SUCCEEDED),
            not_found=statuses.count(EnrichmentStatus.NOT_FOUND),
            transient_failures=statuses.count(EnrichmentStatus.TRANSIENT_FAILURE),
            permanent_failures=statuses.count(EnrichmentStatus.PERMANENT_FAILURE),
            provider_attempts=provider_attempts,
            retries=provider_attempts - public_count,
            scheduled_wait_seconds=scheduled_wait_seconds,
            policy=self.policy,
            records=tuple(records),
        )
