"""Shared behavior contract for endpoint inspection repository implementations."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

from jaws.domain import (
    CaptureId,
    CaptureRecord,
    CaptureSourceKind,
    CaptureState,
    EndpointProfile,
    EnrichmentRecord,
    EnrichmentStatus,
    EntityId,
    ObservationScopeId,
    PacketRecord,
    ProfileIdentity,
)


def _capture(capture_id: CaptureId, registered_at: datetime) -> CaptureRecord:
    return CaptureRecord(
        capture_id=capture_id,
        source_kind=CaptureSourceKind.LIVE_INTERFACE,
        source_name="eth0",
        state=CaptureState.REGISTERED,
        registered_at=registered_at,
    ).transition(CaptureState.RUNNING, registered_at)


def _profile(
    address: str,
    capture_id: CaptureId,
    computed_at: datetime,
    *,
    bytes_out: int,
    bytes_in: int,
) -> EndpointProfile:
    return EndpointProfile(
        identity=ProfileIdentity(
            entity_id=EntityId(f"ip:{address}"),
            scope_id=ObservationScopeId(f"scope_{capture_id.value}"),
            representation_id="inspection-fixture",
            representation_version="1",
            model_id="fixture-model",
            model_revision="fixture-revision",
        ),
        legacy_scope=capture_id.value,
        computed_at=computed_at,
        address_classification="public",
        bytes_out=bytes_out,
        packets_out=2,
        out_peers=1,
        out_ports=(443,),
        bytes_in=bytes_in,
        packets_in=1,
        in_peers=1,
        in_ports=(50000,),
        protocols=("TCP",),
        interval_mean=0.5,
        interval_cv=0.1,
        embedding=(0.1, 0.2),
    )


def assert_inspection_repository_contract(repositories):
    observed_at = datetime(2026, 8, 12, 12, tzinfo=UTC)
    first_id = CaptureId("cap_inspection_fixture_z_first")
    second_id = CaptureId("cap_inspection_fixture_a_second")
    local = "192.0.2.10"
    target = "198.51.100.20"
    other = "203.0.113.30"

    repositories.captures.add(_capture(first_id, observed_at))
    repositories.captures.add(_capture(second_id, observed_at + timedelta(seconds=1)))
    repositories.packets.append(
        first_id,
        (
            PacketRecord(
                capture_id=first_id,
                observed_at=observed_at + timedelta(milliseconds=100),
                protocol="TCP",
                size_bytes=100,
                source_ip=target,
                destination_ip=local,
                source_port=443,
                destination_port=50000,
            ),
            PacketRecord(
                capture_id=first_id,
                observed_at=observed_at + timedelta(milliseconds=200),
                protocol="TCP",
                size_bytes=200,
                source_ip=local,
                destination_ip=target,
                source_port=50001,
                destination_port=443,
            ),
        ),
    )
    repositories.packets.append(
        second_id,
        (
            PacketRecord(
                capture_id=second_id,
                observed_at=observed_at + timedelta(seconds=1, milliseconds=100),
                protocol="TCP",
                size_bytes=300,
                source_ip=target,
                destination_ip=local,
                source_port=443,
                destination_port=50002,
            ),
            PacketRecord(
                capture_id=second_id,
                observed_at=observed_at + timedelta(seconds=1, milliseconds=200),
                protocol="UDP",
                size_bytes=50,
                source_ip=target,
                destination_ip=other,
                source_port=53,
                destination_port=53000,
            ),
        ),
    )
    repositories.enrichment.put(
        EnrichmentRecord(
            entity_id=EntityId(f"ip:{target}"),
            ip_address=target,
            status=EnrichmentStatus.SUCCEEDED,
            acquired_at=observed_at,
            provider_id="fixture",
            provider_revision="1",
            organization="Example Networks",
            hostname="target.example",
            location="Example City",
        )
    )
    repositories.enrichment.put(
        EnrichmentRecord(
            entity_id=EntityId(f"ip:{local}"),
            ip_address=local,
            status=EnrichmentStatus.SUCCEEDED,
            acquired_at=observed_at,
            provider_id="fixture",
            provider_revision="1",
            organization="Local Network",
        )
    )

    first_target = _profile(
        target,
        first_id,
        observed_at + timedelta(seconds=2),
        bytes_out=100,
        bytes_in=200,
    )
    latest_target = _profile(
        target,
        second_id,
        observed_at + timedelta(seconds=3),
        bytes_out=1000,
        bytes_in=300,
    )
    latest_local = _profile(
        local,
        second_id,
        observed_at + timedelta(seconds=3),
        bytes_out=10,
        bytes_in=20,
    )
    repositories.profiles.replace_scope(
        ObservationScopeId(f"scope_{first_id.value}"), (first_target,)
    )
    repositories.profiles.replace_scope(
        ObservationScopeId(f"scope_{second_id.value}"), (latest_local, latest_target)
    )

    recent = repositories.inspection.recent_profiles(
        computed_after=observed_at + timedelta(seconds=2, milliseconds=500), limit=10
    )
    assert [record.identity.entity_id.value for record in recent] == [
        f"ip:{target}",
        f"ip:{local}",
    ]
    assert recent[0].organization == "Example Networks"
    assert recent[0].hostname == "target.example"

    # Recomputing an older capture makes it the latest profile without moving that
    # capture to the front of evidence-time history.
    repositories.profiles.replace_scope(
        ObservationScopeId(f"scope_{first_id.value}"),
        (replace(first_target, computed_at=observed_at + timedelta(seconds=4)),),
    )

    inspection = repositories.inspection.inspect(
        EntityId(f"ip:{target}"), peer_limit=1, packet_limit=2, history_limit=10
    )
    assert inspection.found
    assert inspection.profile is not None
    assert inspection.profile.legacy_scope == first_id.value
    assert inspection.profile.organization == "Example Networks"
    assert [record.legacy_scope for record in inspection.history] == [
        second_id.value,
        first_id.value,
    ]
    assert inspection.total_packets == 4
    assert inspection.total_peers == 2
    assert len(inspection.peers) == 1
    assert inspection.peers[0].peer.ip_address == local
    assert inspection.peers[0].bytes_out == 400
    assert inspection.peers[0].bytes_in == 200
    assert inspection.peers[0].service_ports == (443,)
    assert inspection.peers[0].ephemeral_ports == 3
    assert [record.size_bytes for record in inspection.packets] == [50, 300]

    missing = repositories.inspection.inspect(
        EntityId("ip:203.0.113.99"), peer_limit=1, packet_limit=1, history_limit=1
    )
    assert not missing.found
    assert missing.total_packets == 0
    assert missing.profile is None
