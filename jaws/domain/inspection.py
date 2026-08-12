"""Read-only endpoint inspection projections independent of graph representation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .enrichment import EntityMetadata, normalized_ip
from .identifiers import EntityId
from .profiles import EndpointProfile
from .time import normalize_utc


def _count(value: int, name: str) -> int:
    if value < 0:
        raise ValueError(f"inspection {name} cannot be negative")
    return value


def _ports(values: tuple[int, ...]) -> tuple[int, ...]:
    normalized = tuple(sorted(set(values)))
    if any(not 1 <= value <= 65535 for value in normalized):
        raise ValueError("inspection service ports must be between 1 and 65535")
    return normalized


def _legacy_port(value: int | None) -> int | None:
    if value is None:
        return None
    if not 0 <= value <= 65535:
        raise ValueError("inspection packet ports must be between 0 and 65535")
    return value


@dataclass(frozen=True, slots=True)
class EndpointPeerTraffic:
    """Bidirectional all-session traffic between an inspected IP and one peer."""

    peer: EntityMetadata
    bytes_out: int
    packets_out: int
    bytes_in: int
    packets_in: int
    protocols: tuple[str, ...] = ()
    service_ports: tuple[int, ...] = ()
    ephemeral_ports: int = 0

    def __post_init__(self) -> None:
        for field_name in ("bytes_out", "packets_out", "bytes_in", "packets_in"):
            object.__setattr__(self, field_name, _count(getattr(self, field_name), field_name))
        protocols = tuple(sorted({value.strip() for value in self.protocols}))
        if any(not value for value in protocols):
            raise ValueError("inspection protocols cannot contain empty values")
        object.__setattr__(self, "protocols", protocols)
        object.__setattr__(self, "service_ports", _ports(self.service_ports))
        object.__setattr__(self, "ephemeral_ports", _count(self.ephemeral_ports, "ephemeral_ports"))

    @property
    def bytes_total(self) -> int:
        return self.bytes_out + self.bytes_in


@dataclass(frozen=True, slots=True)
class EndpointPacketSample:
    """Legacy-compatible raw packet projection used by endpoint inspection."""

    source_ip: str
    source_port: int | None
    destination_ip: str
    destination_port: int | None
    protocol: str
    size_bytes: int
    observed_at: datetime

    def __post_init__(self) -> None:
        protocol = self.protocol.strip()
        if not protocol:
            raise ValueError("inspection packet protocol cannot be empty")
        object.__setattr__(self, "source_ip", normalized_ip(self.source_ip))
        object.__setattr__(self, "destination_ip", normalized_ip(self.destination_ip))
        object.__setattr__(self, "source_port", _legacy_port(self.source_port))
        object.__setattr__(self, "destination_port", _legacy_port(self.destination_port))
        object.__setattr__(self, "protocol", protocol)
        object.__setattr__(self, "size_bytes", _count(self.size_bytes, "packet size"))
        object.__setattr__(self, "observed_at", normalize_utc(self.observed_at))


@dataclass(frozen=True, slots=True)
class EndpointInspection:
    """Bounded profile, history, peer, and packet detail for one entity."""

    entity_id: EntityId
    profile: EndpointProfile | None
    total_packets: int
    total_peers: int
    history: tuple[EndpointProfile, ...] = ()
    peers: tuple[EndpointPeerTraffic, ...] = ()
    packets: tuple[EndpointPacketSample, ...] = ()

    def __post_init__(self) -> None:
        if not self.entity_id.value.startswith("ip:"):
            raise ValueError("inspection entity_id must identify an IP address")
        address = normalized_ip(self.entity_id.value.removeprefix("ip:"))
        if self.entity_id != EntityId(f"ip:{address}"):
            raise ValueError("inspection entity_id must contain a normalized IP address")
        if self.profile is not None and self.profile.identity.entity_id != self.entity_id:
            raise ValueError("inspection profile must identify the requested entity")
        if any(record.identity.entity_id != self.entity_id for record in self.history):
            raise ValueError("inspection history must identify the requested entity")
        if any(address not in {record.source_ip, record.destination_ip} for record in self.packets):
            raise ValueError("inspection packet samples must involve the requested entity")
        object.__setattr__(self, "total_packets", _count(self.total_packets, "total_packets"))
        object.__setattr__(self, "total_peers", _count(self.total_peers, "total_peers"))
        object.__setattr__(self, "history", tuple(self.history))
        object.__setattr__(self, "peers", tuple(self.peers))
        object.__setattr__(self, "packets", tuple(self.packets))

    @property
    def found(self) -> bool:
        return self.total_packets > 0 or self.profile is not None
