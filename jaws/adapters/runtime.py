"""Standard runtime implementations of injectable clock and identity ports."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from time import sleep
from uuid import UUID, uuid4

from jaws.domain import AuditEventId, CaptureId


@dataclass(frozen=True, slots=True)
class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class SystemWaitStrategy:
    """Perform real runtime waits requested by deterministic service policy."""

    sleeper: Callable[[float], None] = sleep

    def wait(self, seconds: float) -> None:
        self.sleeper(seconds)


@dataclass(frozen=True, slots=True)
class UuidCaptureIdGenerator:
    """Create opaque capture IDs while permitting deterministic injected UUIDs."""

    uuid_factory: Callable[[], UUID] = uuid4

    def new(self) -> CaptureId:
        return CaptureId(f"cap_{self.uuid_factory().hex}")


@dataclass(frozen=True, slots=True)
class UuidAuditEventIdGenerator:
    """Create opaque audit IDs without using evidence or operator data."""

    uuid_factory: Callable[[], UUID] = uuid4

    def new(self) -> AuditEventId:
        return AuditEventId(f"audit_{self.uuid_factory().hex}")
