"""Repository contracts for versioned capture and packet evidence."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Protocol

from jaws.domain import (
    CaptureId,
    CaptureRecord,
    CaptureState,
    EndpointInspection,
    EndpointProfile,
    EnrichmentRecord,
    EntityId,
    EntityMetadata,
    ObservationScopeId,
    ObservationWindow,
    OutlierStatus,
    PacketRecord,
    ProfileScopeSummary,
    ResearcherAnnotation,
)


def capture_metadata(record: CaptureRecord) -> tuple[object, ...]:
    """Return capture fields that lifecycle transitions may never rewrite."""

    return (
        record.capture_id,
        record.source_kind,
        record.source_name,
        record.registered_at,
        record.legacy_capture_id,
        record.content_digest,
        record.perspective,
        record.capture_filter,
        tuple(record.tool_versions.items()),
    )


class RepositoryError(RuntimeError):
    """Base failure exposed by repository implementations."""


class RepositorySchemaError(RepositoryError):
    """The backing store does not implement the required schema contract."""


class DuplicateCaptureError(RepositoryError):
    """A capture already exists with the requested canonical identity."""


class CaptureNotFoundError(RepositoryError):
    """No capture exists with the requested canonical identity."""


class CaptureStateConflictError(RepositoryError):
    """A lifecycle write did not observe the state expected by its caller."""


class InactiveCaptureError(RepositoryError):
    """Packet evidence cannot be appended to a terminal capture."""


class EntityNotFoundError(RepositoryError):
    """No stored entity exists for enrichment or annotation."""


class ProfileScopeConflictError(RepositoryError):
    """A profile batch does not consistently identify one observation scope."""


class RetentionConflictError(RepositoryError):
    """Retained data changed after a dry-run plan was created."""


class CaptureRepository(Protocol):
    """Lifecycle and catalog operations for immutable capture snapshots."""

    def add(self, record: CaptureRecord) -> None: ...

    def get(self, capture_id: CaptureId) -> CaptureRecord | None: ...

    def find_by_legacy_id(self, legacy_capture_id: str) -> tuple[CaptureRecord, ...]: ...

    def list_all(self) -> tuple[CaptureRecord, ...]: ...

    def transition(self, record: CaptureRecord, *, expected_state: CaptureState) -> None: ...


class PacketRepository(Protocol):
    """Atomic batched writes and bounded capture-scoped packet reads."""

    def append(self, capture_id: CaptureId, records: Sequence[PacketRecord]) -> int: ...

    def read(self, window: ObservationWindow) -> tuple[PacketRecord, ...]: ...

    def read_all(self) -> tuple[PacketRecord, ...]: ...


class EnrichmentRepository(Protocol):
    """Provider observations and researcher annotations for stored IP entities."""

    def count_entities(self) -> int: ...

    def pending_addresses(self) -> tuple[str, ...]: ...

    def get(self, entity_id: EntityId) -> EnrichmentRecord | None: ...

    def list_metadata(self) -> tuple[EntityMetadata, ...]: ...

    def put(self, record: EnrichmentRecord) -> None: ...

    def add_annotation(self, annotation: ResearcherAnnotation) -> None: ...

    def annotations(self, entity_id: EntityId) -> tuple[ResearcherAnnotation, ...]: ...

    def legacy_unknown_addresses(self) -> tuple[str, ...]: ...

    def remove_legacy_unknown_ownership(self, addresses: Sequence[str]) -> int: ...


class ProfileRepository(Protocol):
    """Atomic versioned profile sets, histories, verdicts, and retention."""

    def replace_scope(
        self, scope_id: ObservationScopeId, records: Sequence[EndpointProfile]
    ) -> int: ...

    def read_scope(self, scope_id: ObservationScopeId) -> tuple[EndpointProfile, ...]: ...

    def read_history(self, scope_id: ObservationScopeId) -> tuple[EndpointProfile, ...]: ...

    def list_scopes(self) -> tuple[ProfileScopeSummary, ...]: ...

    def set_outliers(
        self, scope_id: ObservationScopeId, verdicts: Mapping[EntityId, OutlierStatus]
    ) -> int: ...

    def delete_scopes(self, expected: Sequence[ProfileScopeSummary]) -> int: ...


class InspectionRepository(Protocol):
    """Optimized read-only projections for overview and endpoint drill-down tools."""

    def recent_profiles(
        self, *, computed_after: datetime, limit: int
    ) -> tuple[EndpointProfile, ...]: ...

    def inspect(
        self,
        entity_id: EntityId,
        *,
        peer_limit: int,
        packet_limit: int,
        history_limit: int,
    ) -> EndpointInspection: ...
