"""Pure packet-to-endpoint aggregation before representation and embedding."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from math import fsum, isfinite, sqrt
from typing import Protocol

from jaws.domain import (
    ENDPOINT_NUMERIC_FEATURE_SET_V1,
    MIN_TIMING_PACKETS,
    CaptureId,
    EndpointProfileDraft,
    EntityDefinition,
    EntityId,
    EntityMetadata,
    EntityType,
    NumericFeatureSet,
    ObservationWindow,
    classify_ip_address,
)

MAX_PROFILE_PORTS = 20


class UnsupportedEntityDefinitionError(ValueError):
    """The profiler does not implement the declared entity semantics."""


class ProfilingWindowError(ValueError):
    """Supplied packet evidence falls outside its declared observation window."""


class UnsupportedNumericFeatureSetError(ValueError):
    """The profiler does not implement the declared numeric profile contract."""


class ProfilePacketEvidence(Protocol):
    """Read-only packet fields required by aggregation.

    Validated ``PacketRecord`` values satisfy this contract. The legacy pandas adapter may
    project frozen Benchmark 0 rows without promoting them into modern packet evidence.
    """

    @property
    def capture_id(self) -> CaptureId: ...

    @property
    def observed_at(self) -> datetime: ...

    @property
    def protocol(self) -> str: ...

    @property
    def size_bytes(self) -> int: ...

    @property
    def source_ip(self) -> str: ...

    @property
    def destination_ip(self) -> str: ...

    @property
    def source_port(self) -> int | None: ...

    @property
    def destination_port(self) -> int | None: ...


def interval_timing_seconds(
    timestamp_groups: Iterable[Iterable[float]],
    *,
    minimum_packets: int = MIN_TIMING_PACKETS,
) -> tuple[float | None, float | None]:
    """Return pooled within-capture mean and population CV for one packet direction."""

    if minimum_packets < 2:
        raise ValueError("minimum timing packets must be at least two")
    intervals: list[float] = []
    for timestamps in timestamp_groups:
        ordered = sorted(float(value) for value in timestamps if isfinite(float(value)))
        intervals.extend(right - left for left, right in zip(ordered, ordered[1:]))
    if len(intervals) < minimum_packets - 1:
        return None, None
    mean = fsum(intervals) / len(intervals)
    if mean <= 0.0:
        return None, None
    variance = fsum((value - mean) ** 2 for value in intervals) / len(intervals)
    return mean, sqrt(variance) / mean


@dataclass(frozen=True, slots=True)
class EndpointProfilingResult:
    """Profile drafts bound to the exact entity and evidence-selection declarations."""

    entity_definition: EntityDefinition
    observation_window: ObservationWindow
    numeric_feature_set: NumericFeatureSet
    source_packet_count: int
    profiles: tuple[EndpointProfileDraft, ...]

    def __post_init__(self) -> None:
        if self.source_packet_count < 0:
            raise ValueError("profiling source packet count cannot be negative")
        identities = tuple(profile.entity_id for profile in self.profiles)
        if len(set(identities)) != len(identities):
            raise ValueError("profiling result entity identities must be unique")


@dataclass(slots=True)
class _DirectionAggregate:
    bytes: int = 0
    packets: int = 0
    peers: set[str] = field(default_factory=set)
    ports: set[int] = field(default_factory=set)
    protocols: set[str] = field(default_factory=set)
    timestamps: dict[CaptureId, list[float]] = field(default_factory=dict)

    def add(
        self,
        packet: ProfilePacketEvidence,
        *,
        peer: str,
        port: int | None,
    ) -> None:
        self.bytes += packet.size_bytes
        self.packets += 1
        self.peers.add(peer)
        if port is not None:
            self.ports.add(port)
        self.protocols.add(packet.protocol)
        self.timestamps.setdefault(packet.capture_id, []).append(packet.observed_at.timestamp())

    def timing(self, minimum_packets: int) -> tuple[float | None, float | None]:
        groups = (
            self.timestamps[capture_id]
            for capture_id in sorted(self.timestamps, key=lambda value: value.value)
        )
        return interval_timing_seconds(groups, minimum_packets=minimum_packets)


@dataclass(frozen=True, slots=True)
class EndpointProfiler:
    """Aggregate immutable packet evidence into deterministic endpoint profile drafts."""

    minimum_timing_packets: int = MIN_TIMING_PACKETS
    maximum_ports: int = MAX_PROFILE_PORTS
    allow_legacy_negative_packet_sizes: bool = False

    def __post_init__(self) -> None:
        if self.minimum_timing_packets < 2:
            raise ValueError("minimum timing packets must be at least two")
        if self.maximum_ports < 1:
            raise ValueError("maximum profile ports must be positive")
        if not isinstance(self.allow_legacy_negative_packet_sizes, bool):
            raise ValueError("legacy negative-size compatibility must be boolean")

    def profile(
        self,
        packets: Sequence[ProfilePacketEvidence],
        *,
        entity_definition: EntityDefinition,
        observation_window: ObservationWindow,
        numeric_feature_set: NumericFeatureSet,
        metadata: Sequence[EntityMetadata] = (),
    ) -> EndpointProfilingResult:
        """Validate declared semantics and return one address-sorted draft per IP."""

        if (
            entity_definition.entity_type is not EntityType.ENDPOINT_IP
            or entity_definition.version != "1"
        ):
            raise UnsupportedEntityDefinitionError(
                "endpoint profiler supports only entity_type='endpoint_ip', version='1'"
            )
        if numeric_feature_set != ENDPOINT_NUMERIC_FEATURE_SET_V1:
            raise UnsupportedNumericFeatureSetError(
                "endpoint profiler supports only feature_set_id='endpoint_profile_numeric', "
                "version='1'"
            )
        capture_ids = frozenset(observation_window.capture_ids)
        for packet in packets:
            if packet.capture_id not in capture_ids:
                raise ProfilingWindowError(
                    f"packet capture {packet.capture_id} is outside the observation window"
                )
            if (
                observation_window.started_at is not None
                and packet.observed_at < observation_window.started_at
            ):
                raise ProfilingWindowError("packet timestamp precedes the observation window")
            if (
                observation_window.ended_at is not None
                and packet.observed_at > observation_window.ended_at
            ):
                raise ProfilingWindowError("packet timestamp follows the observation window")

        outbound: dict[str, _DirectionAggregate] = {}
        inbound: dict[str, _DirectionAggregate] = {}
        for packet in packets:
            if packet.size_bytes < 0 and not self.allow_legacy_negative_packet_sizes:
                raise ValueError("profile packet size cannot be negative")
            if "0.0.0.0" in {packet.source_ip, packet.destination_ip}:
                continue
            outbound.setdefault(packet.source_ip, _DirectionAggregate()).add(
                packet,
                peer=packet.destination_ip,
                port=packet.destination_port,
            )
            inbound.setdefault(packet.destination_ip, _DirectionAggregate()).add(
                packet,
                peer=packet.source_ip,
                port=packet.destination_port,
            )

        metadata_by_address = {record.ip_address: record for record in metadata}
        drafts: list[EndpointProfileDraft] = []
        for address in sorted(set(outbound) | set(inbound)):
            sent = outbound.get(address, _DirectionAggregate())
            received = inbound.get(address, _DirectionAggregate())
            qualifying_timing = tuple(
                (cv, mean)
                for aggregate in (sent, received)
                for mean, cv in (aggregate.timing(self.minimum_timing_packets),)
                if cv is not None
            )
            interval_mean: float | None = None
            interval_cv: float | None = None
            if qualifying_timing:
                interval_cv, interval_mean = min(qualifying_timing)
            display = metadata_by_address.get(address)
            drafts.append(
                EndpointProfileDraft(
                    entity_id=EntityId(f"ip:{address}"),
                    ip_address=address,
                    address_classification=classify_ip_address(address),
                    organization=display.organization if display else None,
                    hostname=display.hostname if display else None,
                    location=display.location if display else None,
                    bytes_out=sent.bytes,
                    packets_out=sent.packets,
                    out_peers=len(sent.peers),
                    out_ports=tuple(sorted(sent.ports)[: self.maximum_ports]),
                    bytes_in=received.bytes,
                    packets_in=received.packets,
                    in_peers=len(received.peers),
                    in_ports=tuple(sorted(received.ports)[: self.maximum_ports]),
                    protocols=tuple(sorted(sent.protocols | received.protocols)),
                    interval_mean=interval_mean,
                    interval_cv=interval_cv,
                )
            )
        return EndpointProfilingResult(
            entity_definition=entity_definition,
            observation_window=observation_window,
            numeric_feature_set=numeric_feature_set,
            source_packet_count=len(packets),
            profiles=tuple(drafts),
        )
