"""Versioned entity-profile records independent of graph representation."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

from .captures import ProfileIdentity
from .enrichment import AddressClassification, normalized_ip
from .enums import OutlierStatus, ProfileStatus, TimingDirection
from .identifiers import EntityId, ObservationScopeId
from .time import normalize_utc

MIN_TIMING_PACKETS = 6


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


def _optional_text(value: str | None) -> str | None:
    text = value.strip() if value else None
    return text or None


@dataclass(frozen=True, slots=True)
class EndpointProfileDraft:
    """Typed packet aggregation before scope, representation, and embedding are bound."""

    entity_id: EntityId
    ip_address: str
    address_classification: AddressClassification
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
    timing_direction: TimingDirection | None = None

    def __post_init__(self) -> None:
        address = normalized_ip(self.ip_address)
        if self.entity_id != EntityId(f"ip:{address}"):
            raise ValueError("profile draft entity_id must match its normalized IP address")
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
        has_timing = self.interval_mean is not None or self.interval_cv is not None
        if (self.interval_mean is None) != (self.interval_cv is None):
            raise ValueError("profile timing mean and variation must be defined together")
        if has_timing != (self.timing_direction is not None):
            raise ValueError("profile timing direction must accompany timing values")
        object.__setattr__(self, "ip_address", address)
        for field_name in ("organization", "hostname", "location"):
            object.__setattr__(self, field_name, _optional_text(getattr(self, field_name)))
        object.__setattr__(self, "out_ports", _ports(self.out_ports))
        object.__setattr__(self, "in_ports", _ports(self.in_ports))
        object.__setattr__(self, "protocols", _strings(self.protocols, "protocols"))


def host_destination_entity_id(perspective: EntityId, destination_ip: str) -> EntityId:
    """Return the scoped identity for one capture-host to remote-destination entity."""

    if not perspective.value.startswith("ip:"):
        raise ValueError("host-destination perspective must be an IP entity")
    host = normalized_ip(perspective.value.removeprefix("ip:"))
    destination = normalized_ip(destination_ip)
    if host == destination:
        raise ValueError("host-destination peer must differ from its perspective")
    return EntityId(f"host-destination:ip:{host}->ip:{destination}")


@dataclass(frozen=True, slots=True)
class HostDestinationProfileDraft:
    """Host-relative traffic evidence for one explicitly scoped remote destination."""

    entity_id: EntityId
    perspective: EntityId
    destination_ip: str
    address_classification: AddressClassification
    organization: str | None = None
    hostname: str | None = None
    location: str | None = None
    upload_bytes: int = 0
    upload_packets: int = 0
    download_bytes: int = 0
    download_packets: int = 0
    protocols: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        destination = normalized_ip(self.destination_ip)
        if self.entity_id != host_destination_entity_id(self.perspective, destination):
            raise ValueError("host-destination entity_id must match perspective and destination")
        for field_name in (
            "upload_bytes",
            "upload_packets",
            "download_bytes",
            "download_packets",
        ):
            object.__setattr__(self, field_name, _count(getattr(self, field_name), field_name))
        if self.upload_packets == 0:
            raise ValueError("host-destination profiles require outbound host evidence")
        object.__setattr__(self, "destination_ip", destination)
        for field_name in ("organization", "hostname", "location"):
            object.__setattr__(self, field_name, _optional_text(getattr(self, field_name)))
        object.__setattr__(self, "protocols", _strings(self.protocols, "protocols"))


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
