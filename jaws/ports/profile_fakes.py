"""Deterministic enrichment and profile repository implementations."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace

from jaws.domain import (
    AuditEvent,
    EndpointProfile,
    EnrichmentRecord,
    EnrichmentStatus,
    EntityId,
    EntityMetadata,
    ObservationScopeId,
    OutlierStatus,
    ProfileScopeSummary,
    ProfileStatus,
    ResearcherAnnotation,
    normalized_ip,
)

from .repositories import EntityNotFoundError, ProfileScopeConflictError, RetentionConflictError


@dataclass(slots=True)
class InMemoryEnrichmentRepository:
    """Entity inventory with provider records and separate researcher annotations."""

    addresses: tuple[str, ...] = ()
    legacy_unknown_address_set: set[str] = field(default_factory=set)
    metadata_records: tuple[EntityMetadata, ...] = ()
    _records: dict[EntityId, EnrichmentRecord] = field(default_factory=dict)
    _annotations: dict[EntityId, list[ResearcherAnnotation]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.addresses = tuple(sorted({normalized_ip(value) for value in self.addresses}))
        self.legacy_unknown_address_set = {
            normalized_ip(value) for value in self.legacy_unknown_address_set
        }
        if any(record.ip_address not in self.addresses for record in self.metadata_records):
            raise ValueError("metadata records must identify stored addresses")

    def count_entities(self) -> int:
        return len(self.addresses)

    def pending_addresses(self) -> tuple[str, ...]:
        completed = {
            record.ip_address
            for record in self._records.values()
            if record.status
            in {
                EnrichmentStatus.SUCCEEDED,
                EnrichmentStatus.NOT_APPLICABLE,
                EnrichmentStatus.NOT_FOUND,
                EnrichmentStatus.PERMANENT_FAILURE,
            }
        }
        return tuple(
            address
            for address in self.addresses
            if address not in completed and address not in self.legacy_unknown_address_set
        )

    def get(self, entity_id: EntityId) -> EnrichmentRecord | None:
        return self._records.get(entity_id)

    def list_metadata(self) -> tuple[EntityMetadata, ...]:
        metadata = {record.entity_id: record for record in self.metadata_records}
        for record in self._records.values():
            metadata[record.entity_id] = EntityMetadata(
                entity_id=record.entity_id,
                ip_address=record.ip_address,
                organization=record.organization,
                hostname=record.hostname,
                location=record.location,
                coordinates=record.coordinates,
            )
        return tuple(sorted(metadata.values(), key=lambda record: record.ip_address))

    def put(self, record: EnrichmentRecord) -> None:
        if record.ip_address not in self.addresses:
            raise EntityNotFoundError(f"entity does not exist: {record.entity_id}")
        self._records[record.entity_id] = record

    def add_annotation(self, annotation: ResearcherAnnotation) -> None:
        address = annotation.entity_id.value.removeprefix("ip:")
        if address not in self.addresses:
            raise EntityNotFoundError(f"entity does not exist: {annotation.entity_id}")
        current = self._annotations.setdefault(annotation.entity_id, [])
        current[:] = [item for item in current if item.key != annotation.key]
        current.append(annotation)
        current.sort(key=lambda item: (item.recorded_at, item.key))

    def annotations(self, entity_id: EntityId) -> tuple[ResearcherAnnotation, ...]:
        return tuple(self._annotations.get(entity_id, ()))

    def legacy_unknown_addresses(self) -> tuple[str, ...]:
        return tuple(sorted(self.legacy_unknown_address_set))

    def remove_legacy_unknown_ownership(self, addresses: Sequence[str]) -> int:
        requested = {normalized_ip(value) for value in addresses}
        removed = len(self.legacy_unknown_address_set & requested)
        self.legacy_unknown_address_set -= requested
        return removed


@dataclass(slots=True)
class InMemoryProfileRepository:
    """Atomic profile-set replacement with explicit observation-scope identity."""

    _records: dict[ObservationScopeId, tuple[EndpointProfile, ...]] = field(default_factory=dict)

    def replace_scope(
        self, scope_id: ObservationScopeId, records: Sequence[EndpointProfile]
    ) -> int:
        batch = tuple(records)
        if any(record.identity.scope_id != scope_id for record in batch):
            raise ProfileScopeConflictError("every profile must belong to the replaced scope")
        if any(
            record.computed_at is None or record.status is not ProfileStatus.CURRENT
            for record in batch
        ):
            raise ProfileScopeConflictError(
                "new profiles require computed_at and current provenance status"
            )
        keys = tuple(record.profile_key for record in batch)
        if len(set(keys)) != len(keys):
            raise ProfileScopeConflictError("profile keys must be unique within a scope")
        self._records[scope_id] = tuple(
            sorted(batch, key=lambda record: (record.identity.entity_id.value, record.profile_key))
        )
        return len(batch)

    def read_scope(self, scope_id: ObservationScopeId) -> tuple[EndpointProfile, ...]:
        return self._records.get(scope_id, ())

    def read_history(self, scope_id: ObservationScopeId) -> tuple[EndpointProfile, ...]:
        """Return profiles computed before a concrete target scope, oldest first."""

        target = self._records.get(scope_id, ())
        if not target or scope_id.value == "scope_pooled_all":
            return ()
        target_time = max(
            (record.computed_at for record in target if record.computed_at),
            default=None,
        )
        if target_time is None:
            return ()
        history = [
            record
            for records in self._records.values()
            for record in records
            if record.identity.scope_id != scope_id
            and record.identity.scope_id.value != "scope_pooled_all"
            and record.computed_at is not None
            and record.computed_at < target_time
        ]
        return tuple(
            sorted(
                history,
                key=lambda record: (
                    record.computed_at,
                    record.identity.scope_id.value,
                    record.identity.entity_id.value,
                    record.profile_key,
                ),
            )
        )

    def list_scopes(self) -> tuple[ProfileScopeSummary, ...]:
        summaries = []
        for scope_id, records in self._records.items():
            if not records:
                continue
            summaries.append(
                ProfileScopeSummary(
                    scope_id=scope_id,
                    legacy_scope=records[0].legacy_scope,
                    computed_at=max(
                        (record.computed_at for record in records if record.computed_at),
                        default=None,
                    ),
                    profile_count=len(records),
                    status=records[0].status,
                )
            )
        return tuple(
            sorted(
                summaries,
                key=lambda summary: (
                    summary.computed_at is not None,
                    summary.computed_at,
                    summary.scope_id.value,
                ),
                reverse=True,
            )
        )

    def set_outliers(
        self, scope_id: ObservationScopeId, verdicts: Mapping[EntityId, OutlierStatus]
    ) -> int:
        records = self._records.get(scope_id, ())
        known = {record.identity.entity_id for record in records}
        missing = set(verdicts) - known
        if missing:
            raise EntityNotFoundError(
                "profiles do not exist in scope: "
                + ", ".join(sorted(entity.value for entity in missing))
            )
        self._records[scope_id] = tuple(
            replace(record, outlier=verdicts.get(record.identity.entity_id, record.outlier))
            for record in records
        )
        return len(verdicts)

    audit_log: list[AuditEvent] = field(default_factory=list)

    def delete_scopes(
        self,
        expected: Sequence[ProfileScopeSummary],
        *,
        audit_event: AuditEvent | None = None,
    ) -> int:
        requested = tuple(expected)
        if len({summary.scope_id for summary in requested}) != len(requested):
            raise ValueError("retention scope identities must be unique")
        current = {summary.scope_id: summary for summary in self.list_scopes()}
        if any(current.get(summary.scope_id) != summary for summary in requested):
            raise RetentionConflictError("profile scopes changed after retention was planned")
        removed = sum(len(self._records[summary.scope_id]) for summary in requested)
        for summary in requested:
            del self._records[summary.scope_id]
        if audit_event is not None:
            self.audit_log.append(audit_event)
        return removed
