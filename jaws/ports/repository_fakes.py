"""Deterministic in-memory capture and packet repository implementations."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from jaws.domain import (
    ACTIVE_CAPTURE_STATES,
    CAPTURE_TRANSITIONS,
    CaptureId,
    CaptureRecord,
    CaptureState,
    ObservationWindow,
    PacketRecord,
    require_transition,
)

from .repositories import (
    CaptureNotFoundError,
    CaptureRepository,
    CaptureStateConflictError,
    DuplicateCaptureError,
    InactiveCaptureError,
    capture_metadata,
)


@dataclass(slots=True)
class InMemoryCaptureRepository:
    """In-memory lifecycle catalog with optimistic state transitions."""

    _captures: dict[CaptureId, CaptureRecord] = field(default_factory=dict)

    def add(self, record: CaptureRecord) -> None:
        if record.capture_id in self._captures:
            raise DuplicateCaptureError(f"capture already exists: {record.capture_id}")
        self._captures[record.capture_id] = record

    def get(self, capture_id: CaptureId) -> CaptureRecord | None:
        return self._captures.get(capture_id)

    def find_by_legacy_id(self, legacy_capture_id: str) -> tuple[CaptureRecord, ...]:
        identity = legacy_capture_id.strip()
        if not identity:
            raise ValueError("legacy capture ID cannot be empty")
        return tuple(
            sorted(
                (
                    record
                    for record in self._captures.values()
                    if record.legacy_capture_id == identity
                ),
                key=lambda record: (record.registered_at, record.capture_id.value),
            )
        )

    def list_all(self) -> tuple[CaptureRecord, ...]:
        return tuple(
            sorted(
                self._captures.values(),
                key=lambda record: (record.registered_at, record.capture_id.value),
            )
        )

    def transition(self, record: CaptureRecord, *, expected_state: CaptureState) -> None:
        existing = self.get(record.capture_id)
        if existing is None:
            raise CaptureNotFoundError(f"capture does not exist: {record.capture_id}")
        if existing.state is not expected_state:
            raise CaptureStateConflictError(
                f"capture {record.capture_id} is {existing.state}, expected {expected_state}"
            )
        require_transition(expected_state, record.state, CAPTURE_TRANSITIONS)
        if capture_metadata(existing) != capture_metadata(record):
            raise ValueError("capture metadata cannot change during a lifecycle transition")
        self._captures[record.capture_id] = record


@dataclass(slots=True)
class InMemoryPacketRepository:
    """Capture-scoped packet evidence preserving batch atomicity and order."""

    captures: CaptureRepository
    _records: dict[CaptureId, list[PacketRecord]] = field(default_factory=dict)

    def append(self, capture_id: CaptureId, records: Sequence[PacketRecord]) -> int:
        batch = tuple(records)
        if not batch:
            return 0
        if any(record.capture_id != capture_id for record in batch):
            raise ValueError("every packet in a batch must belong to the requested capture")
        capture = self.captures.get(capture_id)
        if capture is None:
            raise CaptureNotFoundError(f"capture does not exist: {capture_id}")
        if capture.state not in ACTIVE_CAPTURE_STATES:
            raise InactiveCaptureError(f"cannot append packets to {capture.state} capture")
        self._records.setdefault(capture_id, []).extend(batch)
        return len(batch)

    def read(self, window: ObservationWindow) -> tuple[PacketRecord, ...]:
        selected: list[PacketRecord] = []
        for capture_id in window.capture_ids:
            records = self._records.get(capture_id, ())
            bounded = (
                record
                for record in records
                if (window.started_at is None or record.observed_at >= window.started_at)
                and (window.ended_at is None or record.observed_at <= window.ended_at)
            )
            selected.extend(sorted(bounded, key=lambda record: record.observed_at))
        return tuple(selected)
