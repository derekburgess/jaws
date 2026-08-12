"""Versioned entity-profile records independent of graph representation."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

from .captures import ProfileIdentity
from .enums import OutlierStatus, ProfileStatus
from .identifiers import ObservationScopeId
from .time import normalize_utc


def _count(value: int, name: str) -> int:
    if value < 0:
        raise ValueError(f"profile {name} cannot be negative")
    return value


def _ports(values: tuple[int, ...]) -> tuple[int, ...]:
    normalized = tuple(sorted(set(values)))
    if any(not 1 <= value <= 65535 for value in normalized):
        raise ValueError("profile ports must be between 1 and 65535")
    return normalized


def _strings(values: tuple[str, ...], name: str) -> tuple[str, ...]:
    normalized = tuple(sorted({value.strip() for value in values}))
    if any(not value for value in normalized):
        raise ValueError(f"profile {name} cannot contain empty values")
    return normalized


@dataclass(frozen=True, slots=True)
class EndpointProfile:
    """One endpoint representation in one explicit observation scope."""

    identity: ProfileIdentity
    legacy_scope: str
    computed_at: datetime | None
    address_classification: str
    organization: str | None = None
    hostname: str | None = None
    location: str | None = None
    bytes_out: int = 0
    packets_out: int = 0
    out_peers: int = 0
    out_ports: tuple[int, ...] = ()
    bytes_in: int = 0
    packets_in: int = 0
    in_peers: int = 0
    in_ports: tuple[int, ...] = ()
    protocols: tuple[str, ...] = ()
    interval_mean: float | None = None
    interval_cv: float | None = None
    embedding: tuple[float, ...] = ()
    outlier: OutlierStatus = OutlierStatus.NOT_SCORED
    status: ProfileStatus = ProfileStatus.CURRENT

    def __post_init__(self) -> None:
        scope = self.legacy_scope.strip()
        classification = self.address_classification.strip()
        if not scope or not classification:
            raise ValueError("profile legacy scope and address classification cannot be empty")
        for field_name in (
            "bytes_out",
            "packets_out",
            "out_peers",
            "bytes_in",
            "packets_in",
            "in_peers",
        ):
            object.__setattr__(self, field_name, _count(getattr(self, field_name), field_name))
        for field_name in ("interval_mean", "interval_cv"):
            value = getattr(self, field_name)
            if value is not None and (not math.isfinite(value) or value < 0):
                raise ValueError(f"profile {field_name} must be finite and nonnegative")
        if any(not math.isfinite(value) for value in self.embedding):
            raise ValueError("profile embedding values must be finite")
        object.__setattr__(self, "legacy_scope", scope)
        object.__setattr__(self, "address_classification", classification)
        if self.computed_at is not None:
            object.__setattr__(self, "computed_at", normalize_utc(self.computed_at))
        object.__setattr__(self, "out_ports", _ports(self.out_ports))
        object.__setattr__(self, "in_ports", _ports(self.in_ports))
        object.__setattr__(self, "protocols", _strings(self.protocols, "protocols"))
        object.__setattr__(self, "embedding", tuple(self.embedding))

    @property
    def profile_key(self) -> str:
        return self.identity.profile_key


@dataclass(frozen=True, slots=True)
class ProfileScopeSummary:
    scope_id: ObservationScopeId
    legacy_scope: str
    computed_at: datetime | None
    profile_count: int
    status: ProfileStatus = ProfileStatus.CURRENT

    def __post_init__(self) -> None:
        legacy_scope = self.legacy_scope.strip()
        if not legacy_scope:
            raise ValueError("profile scope legacy identity cannot be empty")
        if self.profile_count < 0:
            raise ValueError("profile scope count cannot be negative")
        object.__setattr__(self, "legacy_scope", legacy_scope)
        if self.computed_at is not None:
            object.__setattr__(self, "computed_at", normalize_utc(self.computed_at))
