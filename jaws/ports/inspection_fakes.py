"""Deterministic in-memory endpoint inspection repository."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime

from jaws.domain import (
    CaptureId,
    EndpointInspection,
    EndpointPacketSample,
    EndpointPeerTraffic,
    EndpointProfile,
    EntityId,
    EntityMetadata,
    ObservationScopeId,
    normalize_utc,
)

from .repositories import (
    CaptureRepository,
    EnrichmentRepository,
    PacketRepository,
    ProfileRepository,
)


def _bounded_limit(value: int, name: str) -> int:
    if value < 0:
        raise ValueError(f"{name} cannot be negative")
    return value


def _profile_time(record: EndpointProfile) -> tuple[bool, datetime, str]:
    return (
        record.computed_at is not None,
        record.computed_at or datetime.min.replace(tzinfo=UTC),
        record.legacy_scope,
    )


@dataclass(slots=True)
class _PeerAccumulator:
    bytes_out: int = 0
    packets_out: int = 0
    bytes_in: int = 0
    packets_in: int = 0
    protocols: set[str] = field(default_factory=set)
    service_ports: set[int] = field(default_factory=set)
    high_ports: set[int] = field(default_factory=set)


@dataclass(slots=True)
class InMemoryInspectionRepository:
    """Compose existing typed repositories into bounded inspection projections."""

    profiles: ProfileRepository
    packets: PacketRepository
    enrichment: EnrichmentRepository
    captures: CaptureRepository

    def _metadata(self) -> dict[EntityId, EntityMetadata]:
        return {record.entity_id: record for record in self.enrichment.list_metadata()}

    @staticmethod
    def _with_metadata(
        profile: EndpointProfile, metadata: dict[EntityId, EntityMetadata]
    ) -> EndpointProfile:
        fallback = metadata.get(profile.identity.entity_id)
        if fallback is None:
            return profile
        return replace(
            profile,
            organization=profile.organization or fallback.organization,
            hostname=profile.hostname or fallback.hostname,
            location=profile.location or fallback.location,
        )

    def recent_profiles(
        self, *, computed_after: datetime, limit: int
    ) -> tuple[EndpointProfile, ...]:
        bound = _bounded_limit(limit, "profile limit")
        threshold = normalize_utc(computed_after)
        scopes = self.profiles.list_scopes()
        if not scopes or bound == 0:
            return ()
        metadata = self._metadata()
        records = (
            self._with_metadata(record, metadata)
            for record in self.profiles.read_scope(scopes[0].scope_id)
            if record.computed_at is not None and record.computed_at > threshold
        )
        return tuple(
            sorted(
                records,
                key=lambda record: (
                    -(record.bytes_out + record.bytes_in),
                    record.identity.entity_id.value,
                ),
            )[:bound]
        )

    def profile(self, entity_id: EntityId, capture_id: CaptureId) -> EndpointProfile | None:
        metadata = self._metadata()
        scope = ObservationScopeId(f"scope_{capture_id.value}")
        return next(
            (
                self._with_metadata(record, metadata)
                for record in self.profiles.read_scope(scope)
                if record.identity.entity_id == entity_id
            ),
            None,
        )

    def inspect(
        self,
        entity_id: EntityId,
        *,
        peer_limit: int,
        packet_limit: int,
        history_limit: int,
    ) -> EndpointInspection:
        peer_bound = _bounded_limit(peer_limit, "peer limit")
        packet_bound = _bounded_limit(packet_limit, "packet limit")
        history_bound = _bounded_limit(history_limit, "history limit")
        address = entity_id.value.removeprefix("ip:")
        metadata = self._metadata()

        profiles = [
            self._with_metadata(record, metadata)
            for scope in self.profiles.list_scopes()
            for record in self.profiles.read_scope(scope.scope_id)
            if record.identity.entity_id == entity_id
        ]
        profiles.sort(key=_profile_time, reverse=True)
        profile = profiles[0] if profiles else None
        capture_times = {
            record.capture_id.value: record.started_at or record.registered_at
            for record in self.captures.list_all()
        }
        historical = [
            record
            for record in profiles
            if record.legacy_scope != "all" and record.identity.scope_id.value != "scope_pooled_all"
        ]
        historical.sort(
            key=lambda record: (
                capture_times.get(
                    record.legacy_scope,
                    record.computed_at or datetime.min.replace(tzinfo=UTC),
                ),
                record.legacy_scope,
            ),
            reverse=True,
        )
        history = tuple(historical[:history_bound])

        packets = tuple(
            record
            for record in self.packets.read_all()
            if record.source_ip == address or record.destination_ip == address
        )
        peer_addresses = {
            record.destination_ip if record.source_ip == address else record.source_ip
            for record in packets
        }
        grouped: defaultdict[str, _PeerAccumulator] = defaultdict(_PeerAccumulator)
        for record in packets:
            outbound = record.source_ip == address
            peer_address = record.destination_ip if outbound else record.source_ip
            values = grouped[peer_address]
            if outbound:
                values.bytes_out += record.size_bytes
                values.packets_out += 1
            else:
                values.bytes_in += record.size_bytes
                values.packets_in += 1
            values.protocols.add(record.protocol)
            if record.source_port is not None and record.destination_port is not None:
                values.service_ports.add(min(record.source_port, record.destination_port))
                values.high_ports.add(max(record.source_port, record.destination_port))

        peers = []
        for peer_address, values in grouped.items():
            peer_metadata = metadata.get(EntityId(f"ip:{peer_address}")) or EntityMetadata(
                entity_id=EntityId(f"ip:{peer_address}"),
                ip_address=peer_address,
            )
            peers.append(
                EndpointPeerTraffic(
                    peer=peer_metadata,
                    bytes_out=values.bytes_out,
                    packets_out=values.packets_out,
                    bytes_in=values.bytes_in,
                    packets_in=values.packets_in,
                    protocols=tuple(values.protocols),
                    service_ports=tuple(values.service_ports),
                    ephemeral_ports=len(values.high_ports - values.service_ports),
                )
            )
        peers.sort(key=lambda record: (-record.bytes_total, record.peer.ip_address))

        recent_packets = sorted(
            packets,
            key=lambda record: (
                record.observed_at,
                record.capture_id.value,
                record.source_ip,
                record.destination_ip,
            ),
            reverse=True,
        )[:packet_bound]
        samples = tuple(
            EndpointPacketSample(
                source_ip=record.source_ip,
                source_port=record.source_port or 0,
                destination_ip=record.destination_ip,
                destination_port=record.destination_port or 0,
                protocol=record.protocol,
                size_bytes=record.size_bytes,
                observed_at=record.observed_at,
            )
            for record in recent_packets
        )
        return EndpointInspection(
            entity_id=entity_id,
            profile=profile,
            total_packets=len(packets),
            total_peers=len(peer_addresses),
            history=history,
            peers=tuple(peers[:peer_bound]),
            packets=samples,
        )
