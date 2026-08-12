"""Capture evidence identity, lifecycle, provenance, and observation-scope records."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime
from types import MappingProxyType

from .enums import CaptureSourceKind, CaptureState, ObservationScopeKind
from .identifiers import CanonicalDigest, CaptureId, EntityId, ObservationScopeId
from .serialization import canonical_digest
from .time import CAPTURE_TRANSITIONS, normalize_utc, require_transition

TERMINAL_CAPTURE_STATES = frozenset(
    {
        CaptureState.COMPLETE,
        CaptureState.PARTIAL,
        CaptureState.FAILED,
        CaptureState.CANCELLED,
    }
)
ACTIVE_CAPTURE_STATES = frozenset({CaptureState.RUNNING, CaptureState.IMPORTING})


def capture_scope_id(capture_id: CaptureId) -> ObservationScopeId:
    """Derive the stable single-capture scope identity used by storage adapters."""

    return ObservationScopeId(f"scope_{capture_id.value}")


def _tool_versions(value: Mapping[str, str]) -> Mapping[str, str]:
    normalized: dict[str, str] = {}
    for name, version in value.items():
        key = name.strip()
        item = version.strip()
        if not key or not item:
            raise ValueError("tool version names and values cannot be empty")
        normalized[key] = item
    return MappingProxyType(dict(sorted(normalized.items())))


@dataclass(frozen=True, slots=True)
class CaptureRecord:
    """One immutable snapshot of a bounded live capture or PCAP import."""

    capture_id: CaptureId
    source_kind: CaptureSourceKind
    source_name: str
    state: CaptureState
    registered_at: datetime
    legacy_capture_id: str | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    packet_count: int = 0
    content_digest: CanonicalDigest | None = None
    perspective: EntityId | None = None
    capture_filter: str | None = None
    tool_versions: Mapping[str, str] = field(default_factory=dict)
    failure_code: str | None = None

    def __post_init__(self) -> None:
        source_name = self.source_name.strip()
        if not source_name:
            raise ValueError("capture source_name cannot be empty")
        if self.packet_count < 0:
            raise ValueError("capture packet_count cannot be negative")
        object.__setattr__(self, "source_name", source_name)
        object.__setattr__(self, "registered_at", normalize_utc(self.registered_at))
        for field_name in ("started_at", "ended_at"):
            value = getattr(self, field_name)
            if value is not None:
                object.__setattr__(self, field_name, normalize_utc(value))
        if self.started_at is not None and self.started_at < self.registered_at:
            raise ValueError("capture started before registration")
        lower_bound = self.started_at or self.registered_at
        if self.ended_at is not None and self.ended_at < lower_bound:
            raise ValueError("capture ended before it started")
        if self.state in ACTIVE_CAPTURE_STATES and self.started_at is None:
            raise ValueError("active capture requires started_at")
        if (
            self.state in TERMINAL_CAPTURE_STATES
            and self.ended_at is None
            and self.source_kind is not CaptureSourceKind.LEGACY_UNKNOWN
        ):
            raise ValueError(
                "terminal capture requires ended_at unless legacy provenance is unknown"
            )
        if (
            self.state is CaptureState.RUNNING
            and self.source_kind is not CaptureSourceKind.LIVE_INTERFACE
        ):
            raise ValueError("running state requires a live-interface source")
        if (
            self.state is CaptureState.IMPORTING
            and self.source_kind is not CaptureSourceKind.PCAP_FILE
        ):
            raise ValueError("importing state requires a PCAP-file source")
        legacy = self.legacy_capture_id.strip() if self.legacy_capture_id else None
        object.__setattr__(self, "legacy_capture_id", legacy)
        if self.content_digest is not None:
            digest = str(self.content_digest)
            if len(digest) != 64 or any(
                character not in "0123456789abcdef" for character in digest
            ):
                raise ValueError("capture content_digest must be lowercase SHA-256 text")
        capture_filter = self.capture_filter.strip() if self.capture_filter else None
        object.__setattr__(self, "capture_filter", capture_filter)
        failure_code = self.failure_code.strip() if self.failure_code else None
        object.__setattr__(self, "failure_code", failure_code)
        object.__setattr__(self, "tool_versions", _tool_versions(self.tool_versions))

    def transition(
        self,
        target: CaptureState,
        at: datetime,
        *,
        packet_count: int | None = None,
        failure_code: str | None = None,
    ) -> CaptureRecord:
        """Return the next validated snapshot under the declared lifecycle graph."""

        require_transition(self.state, target, CAPTURE_TRANSITIONS)
        timestamp = normalize_utc(at)
        started_at = timestamp if target in ACTIVE_CAPTURE_STATES else self.started_at
        ended_at = timestamp if target in TERMINAL_CAPTURE_STATES else self.ended_at
        return replace(
            self,
            state=target,
            started_at=started_at,
            ended_at=ended_at,
            packet_count=self.packet_count if packet_count is None else packet_count,
            failure_code=self.failure_code if failure_code is None else failure_code,
        )


@dataclass(frozen=True, slots=True)
class ObservationScope:
    """Explicit identity for one declared set of capture evidence."""

    scope_id: ObservationScopeId
    kind: ObservationScopeKind
    capture_ids: tuple[CaptureId, ...]
    created_at: datetime
    perspective: EntityId | None = None
    filters: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.capture_ids:
            raise ValueError("observation scope requires at least one capture")
        if len(set(self.capture_ids)) != len(self.capture_ids):
            raise ValueError("observation scope capture IDs must be unique")
        if self.kind is ObservationScopeKind.CAPTURE and len(self.capture_ids) != 1:
            raise ValueError("single-capture scope requires exactly one capture")
        normalized_filters = tuple(value.strip() for value in self.filters)
        if any(not value for value in normalized_filters):
            raise ValueError("observation scope filters cannot be empty")
        object.__setattr__(self, "created_at", normalize_utc(self.created_at))
        object.__setattr__(self, "filters", normalized_filters)

    @classmethod
    def for_capture(
        cls,
        capture_id: CaptureId,
        created_at: datetime,
        *,
        perspective: EntityId | None = None,
        filters: tuple[str, ...] = (),
    ) -> ObservationScope:
        return cls(
            scope_id=capture_scope_id(capture_id),
            kind=ObservationScopeKind.CAPTURE,
            capture_ids=(capture_id,),
            created_at=created_at,
            perspective=perspective,
            filters=filters,
        )


@dataclass(frozen=True, slots=True)
class ProfileIdentity:
    """Uniqueness inputs for one entity representation in one observation scope."""

    entity_id: EntityId
    scope_id: ObservationScopeId
    representation_id: str
    representation_version: str
    model_id: str | None = None
    model_revision: str | None = None

    def __post_init__(self) -> None:
        for field_name in ("representation_id", "representation_version"):
            value = getattr(self, field_name).strip()
            if not value:
                raise ValueError(f"{field_name} cannot be empty")
            object.__setattr__(self, field_name, value)
        model_id = self.model_id.strip() if self.model_id else None
        model_revision = self.model_revision.strip() if self.model_revision else None
        if (model_id is None) != (model_revision is None):
            raise ValueError("model_id and model_revision must be provided together")
        object.__setattr__(self, "model_id", model_id)
        object.__setattr__(self, "model_revision", model_revision)

    @property
    def profile_key(self) -> str:
        return "profile_" + str(canonical_digest(self))
