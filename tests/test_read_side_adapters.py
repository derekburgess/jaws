"""Offline parity checks for repository-backed legacy compute/finder reads."""

from datetime import UTC, datetime, timedelta

from jaws import jaws_compute, jaws_finder
from jaws.domain import (
    CaptureId,
    CaptureRecord,
    CaptureSourceKind,
    CaptureState,
    EndpointProfile,
    EntityId,
    EntityMetadata,
    ObservationScopeId,
    PacketRecord,
    ProfileIdentity,
)
from jaws.ports import (
    InMemoryCaptureRepository,
    InMemoryEnrichmentRepository,
    InMemoryPacketRepository,
    InMemoryProfileRepository,
)


def _repositories():
    observed_at = datetime(2026, 8, 12, 12, tzinfo=UTC)
    first_id = CaptureId("cap_read_fixture_z_first")
    second_id = CaptureId("cap_read_fixture_a_second")
    captures = InMemoryCaptureRepository()
    captures.add(
        CaptureRecord(
            capture_id=second_id,
            source_kind=CaptureSourceKind.LIVE_INTERFACE,
            source_name="eth0",
            state=CaptureState.RUNNING,
            registered_at=observed_at + timedelta(seconds=1),
            started_at=observed_at + timedelta(seconds=1),
        )
    )
    captures.add(
        CaptureRecord(
            capture_id=first_id,
            source_kind=CaptureSourceKind.LIVE_INTERFACE,
            source_name="eth0",
            state=CaptureState.RUNNING,
            registered_at=observed_at,
            started_at=observed_at,
        )
    )
    packets = InMemoryPacketRepository(captures)
    packets.append(
        first_id,
        (
            PacketRecord(
                capture_id=first_id,
                observed_at=observed_at,
                protocol="TCP",
                size_bytes=100,
                source_ip="10.0.0.2",
                destination_ip="8.8.8.8",
                source_port=50000,
                destination_port=443,
            ),
            PacketRecord(
                capture_id=first_id,
                observed_at=observed_at + timedelta(milliseconds=100),
                protocol="TCP",
                size_bytes=60,
                source_ip="8.8.8.8",
                destination_ip="10.0.0.2",
                source_port=443,
                destination_port=50000,
            ),
        ),
    )
    packets.append(
        second_id,
        (
            PacketRecord(
                capture_id=second_id,
                observed_at=observed_at + timedelta(seconds=1),
                protocol="UDP",
                size_bytes=50,
                source_ip="10.0.0.2",
                destination_ip="1.1.1.1",
                destination_port=53,
            ),
        ),
    )
    metadata = (
        EntityMetadata(
            entity_id=EntityId("ip:10.0.0.2"),
            ip_address="10.0.0.2",
            organization=jaws_finder.LOCAL_ORG,
            hostname="fixture-host",
        ),
        EntityMetadata(
            entity_id=EntityId("ip:8.8.8.8"),
            ip_address="8.8.8.8",
            organization="AS15169 Google LLC",
            hostname="dns.google",
            location="Mountain View",
        ),
        EntityMetadata(
            entity_id=EntityId("ip:1.1.1.1"),
            ip_address="1.1.1.1",
            organization="Cloudflare",
        ),
        EntityMetadata(
            entity_id=EntityId("ip:224.0.0.251"),
            ip_address="224.0.0.251",
        ),
    )
    enrichment = InMemoryEnrichmentRepository(
        addresses=tuple(record.ip_address for record in metadata),
        metadata_records=metadata,
    )
    profiles = InMemoryProfileRepository()
    scope_id = ObservationScopeId(f"scope_{second_id.value}")

    def profile(address: str, *, embedding=(0.1, 0.2)) -> EndpointProfile:
        return EndpointProfile(
            identity=ProfileIdentity(
                entity_id=EntityId(f"ip:{address}"),
                scope_id=scope_id,
                representation_id="endpoint-description",
                representation_version="legacy-v1",
                model_id="fixture-model",
                model_revision="fixture-revision",
            ),
            legacy_scope=second_id.value,
            computed_at=observed_at + timedelta(seconds=2),
            address_classification="fixture",
            bytes_out=100,
            packets_out=2,
            out_peers=1,
            bytes_in=50,
            packets_in=1,
            in_peers=1,
            embedding=embedding,
        )

    profiles.replace_scope(
        scope_id,
        (
            profile("8.8.8.8"),
            profile("10.0.0.2"),
            profile("224.0.0.251"),
        ),
    )
    return captures, packets, enrichment, profiles, first_id, second_id


def test_compute_reads_capture_order_packets_and_metadata_through_repositories():
    captures, packets, enrichment, _, first_id, second_id = _repositories()

    assert jaws_compute.resolve_session(None, "fixture", "latest", captures) == (
        second_id.value,
        [first_id.value, second_id.value],
    )
    frame = jaws_compute.fetch_packets(None, "fixture", first_id.value, packets)
    assert frame["src_ip"].tolist() == ["10.0.0.2", "8.8.8.8"]
    assert frame["capture_id"].tolist() == [first_id.value, first_id.value]
    assert jaws_compute.fetch_ip_metadata(None, "fixture", enrichment)["8.8.8.8"] == {
        "ip_address": "8.8.8.8",
        "org": "AS15169 Google LLC",
        "hostname": "dns.google",
        "location": "Mountain View",
    }


def test_finder_reads_profiles_ports_and_host_outbound_through_repositories():
    _, packets, enrichment, profiles, first_id, second_id = _repositories()

    embeddings, data, excluded_local, excluded_nc = jaws_finder.fetch_data_for_dbscan(
        None,
        "fixture",
        False,
        second_id.value,
        profiles,
        enrichment,
    )
    assert [value.tolist() for value in embeddings] == [[0.1, 0.2]]
    assert [record["ip_address"] for record in data] == ["8.8.8.8"]
    assert data[0]["org"] == "AS15169 Google LLC"
    assert excluded_local == 1
    assert excluded_nc == [{"ip_address": "224.0.0.251", "endpoint_type": "multicast"}]

    assert jaws_finder.fetch_data_for_portsize(None, "fixture", packets) == [
        {"size": 100, "src_port": 50000, "dst_port": 443},
        {"size": 60, "src_port": 443, "dst_port": 50000},
    ]
    local_ips, rows = jaws_finder.fetch_host_outbound(
        None, "fixture", first_id.value, packets, enrichment
    )
    assert local_ips == ["10.0.0.2"]
    assert rows == [
        {
            "ip_address": "8.8.8.8",
            "upload_bytes": 100,
            "upload_packets": 1,
            "download_bytes": 60,
            "download_packets": 1,
            "org": "AS15169 Google LLC",
            "hostname": "dns.google",
            "location": "Mountain View",
        }
    ]
