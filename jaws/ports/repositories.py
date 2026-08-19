"""Repository contracts for versioned capture and packet evidence."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Protocol

from jaws.domain import (
    AdministrationPlan,
    AuditEvent,
    CaptureId,
    CaptureRecord,
    CaptureState,
    EndpointInspection,
    EndpointProfile,
    EnrichmentRecord,
    EntityId,
    EntityMetadata,
    EvidenceSchemaProvenance,
    EvidenceSnapshot,
    ExperimentId,
    ExperimentRunIndex,
    FindingId,
    FindingIndex,
    FindingIndexBatch,
    ObservationScopeId,
    ObservationWindow,
    OutlierStatus,
    PacketRecord,
    ProfileScopeSummary,
    ResearcherAnnotation,
    RunId,
    RunState,
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


class EvidenceSchemaConflictError(RepositoryError):
    """An evidence bundle and target database have different schema provenance."""


class EvidenceImportConflictError(RepositoryError):
    """An evidence import target is not empty or changed after planning."""


class AdministrationConflictError(RepositoryError):
    """A destructive-operation target changed after its plan was created."""


class DuplicateRunError(RepositoryError):
    """An append-only experiment run already has the requested identity."""


class RunNotFoundError(RepositoryError):
    """No indexed experiment run exists with the requested identity."""


class RunStateConflictError(RepositoryError):
    """A run lifecycle write did not observe the caller's expected state."""


class ExperimentDigestConflictError(RepositoryError):
    """An experiment identity is already bound to different semantic content."""


class DuplicateFindingError(RepositoryError):
    """A finding index identity is already bound to another publication."""


class FindingIndexConflictError(RepositoryError):
    """Published finding metadata differs from the canonical run artifact or prior index."""


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

    def delete_scopes(
        self,
        expected: Sequence[ProfileScopeSummary],
        *,
        audit_event: AuditEvent | None = None,
    ) -> int: ...


class AdministrationRepository(Protocol):
    """Database-bound planning, atomic erasure, and durable audit reads."""

    def plan(self) -> AdministrationPlan: ...

    def erase(self, expected: AdministrationPlan, audit_event: AuditEvent) -> tuple[int, int]: ...

    def audit_events(self, *, limit: int = 100) -> tuple[AuditEvent, ...]: ...


class EvidenceRepository(Protocol):
    """Exact versioned evidence snapshots and empty-target atomic restoration."""

    def schema_provenance(self) -> EvidenceSchemaProvenance: ...

    def snapshot(self) -> EvidenceSnapshot: ...

    def is_empty(self) -> bool: ...

    def restore(self, evidence: EvidenceSnapshot) -> None: ...


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


class ExperimentIndexRepository(Protocol):
    """Append-only run discovery metadata for canonical external artifacts."""

    def add(self, record: ExperimentRunIndex) -> None: ...

    def get(self, run_id: RunId) -> ExperimentRunIndex | None: ...

    def list_for_experiment(
        self, experiment_id: ExperimentId
    ) -> tuple[ExperimentRunIndex, ...]: ...

    def list_all(self) -> tuple[ExperimentRunIndex, ...]: ...

    def transition(self, record: ExperimentRunIndex, *, expected_state: RunState) -> None: ...


class FindingRepository(Protocol):
    """Optional, reconstructable discovery index over canonical ranked findings."""

    def publish(self, batch: FindingIndexBatch) -> int: ...

    def get(self, finding_id: FindingId) -> FindingIndex | None: ...

    def list_for_run(self, run_id: RunId) -> tuple[FindingIndex, ...]: ...

    def list_for_entity(self, entity_id: EntityId) -> tuple[FindingIndex, ...]: ...
