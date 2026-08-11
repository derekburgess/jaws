"""Standard runtime implementations of injectable clock and identity ports."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from jaws.domain import CaptureId


@dataclass(frozen=True, slots=True)
class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class UuidCaptureIdGenerator:
    """Create opaque capture IDs while permitting deterministic injected UUIDs."""

    uuid_factory: Callable[[], UUID] = uuid4

    def new(self) -> CaptureId:
        return CaptureId(f"cap_{self.uuid_factory().hex}")
