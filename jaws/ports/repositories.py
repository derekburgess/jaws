"""Repository contracts for versioned capture and packet evidence."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from jaws.domain import (
    CaptureId,
    CaptureRecord,
    CaptureState,
    ObservationWindow,
    PacketRecord,
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
