"""Deterministic provider-neutral enrichment acquisition and persistence."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from jaws.domain import (
    Clock,
    EnrichmentObservation,
    EnrichmentRecord,
    EnrichmentStatus,
    EntityId,
    classify_ip_address,
)
from jaws.ports import EnrichmentProvider, EnrichmentRepository

CLASSIFIER_PROVIDER_ID = "jaws-address-classifier"
CLASSIFIER_PROVIDER_REVISION = "1"

EnrichmentObserver = Callable[[EnrichmentRecord], None]


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

    def enrich_pending(
        self, *, observer: EnrichmentObserver | None = None
    ) -> EnrichmentBatchResult:
        cleanup_legacy_unknown(self.repository)

        total_addresses = self.repository.count_entities()
        pending = self.repository.pending_addresses()
        already_documented = total_addresses - len(pending)
        records: list[EnrichmentRecord] = []
        public_count = 0

        for address in pending:
            classification = classify_ip_address(address)
            if classification == "public":
                public_count += 1
                observation = self.provider.enrich(EntityId(f"ip:{address}"))
                if observation.status is EnrichmentStatus.NOT_APPLICABLE:
                    raise ValueError("public enrichment provider returned not-applicable")
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
            records=tuple(records),
        )
