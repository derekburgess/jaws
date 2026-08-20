"""Pure packet-to-endpoint profiling and legacy projection parity."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pandas as pd
import pytest

from jaws.domain import (
    ENDPOINT_NUMERIC_FEATURE_SET_V1,
    CaptureId,
    EntityDefinition,
    EntityId,
    EntityMetadata,
    EntityType,
    ObservationWindow,
    PacketRecord,
    TimingDirection,
)
from jaws.jaws_compute import (
    build_endpoint_profiles,
    endpoint_timing,
    profile_observation_window,
)
from jaws.services import (
    EndpointProfiler,
    ProfilingWindowError,
    UnsupportedEntityDefinitionError,
    UnsupportedNumericFeatureSetError,
    interval_timing_seconds,
)

NOW = datetime(2026, 8, 20, 12, tzinfo=UTC)
CAPTURE = CaptureId("cap_profile_service")
ENDPOINT_IP_V1 = EntityDefinition(entity_type=EntityType.ENDPOINT_IP, version="1")


def _packet(
    seconds: float,
    source: str,
    destination: str,
    *,
    capture_id: CaptureId = CAPTURE,
    size: int = 100,
    protocol: str = "TCP",
    source_port: int | None = 50000,
    destination_port: int | None = 443,
) -> PacketRecord:
    return PacketRecord(
        capture_id=capture_id,
        observed_at=NOW + timedelta(seconds=seconds),
        protocol=protocol,
        size_bytes=size,
        source_ip=source,
        destination_ip=destination,
        source_port=source_port,
        destination_port=destination_port,
    )


def _window(*packets, capture_ids=None, started_at=None, ended_at=None):
    captures = (
        tuple(capture_ids)
        if capture_ids is not None
        else tuple(sorted({packet.capture_id for packet in packets} or {CAPTURE}))
    )
    return ObservationWindow(
        capture_ids=captures,
        started_at=started_at,
        ended_at=ended_at,
    )


def _profiles(packets, metadata=(), *, profiler=None, window=None, entity=ENDPOINT_IP_V1):
    service = profiler or EndpointProfiler()
    result = service.profile(
        packets,
        entity_definition=entity,
        observation_window=window or _window(*packets),
        numeric_feature_set=ENDPOINT_NUMERIC_FEATURE_SET_V1,
        metadata=metadata,
    )
    return result.profiles


def test_profiler_preserves_directional_counts_peers_ports_protocols_and_metadata():
    metadata = (
        EntityMetadata(
            entity_id=EntityId("ip:10.0.0.2"),
            ip_address="10.0.0.2",
            organization="Local Lab",
            hostname="sensor",
            location="Rack 1",
        ),
    )
    packets = (
        _packet(0, "10.0.0.2", "8.8.8.8", size=100, destination_port=443),
        _packet(
            1,
            "10.0.0.2",
            "1.1.1.1",
            size=50,
            protocol="UDP",
            destination_port=53,
        ),
        _packet(
            2,
            "8.8.8.8",
            "10.0.0.2",
            size=200,
            source_port=443,
            destination_port=50000,
        ),
        _packet(3, "0.0.0.0", "10.0.0.2", size=999),
    )

    drafts = _profiles(packets, metadata)
    by_address = {draft.ip_address: draft for draft in drafts}
    local = by_address["10.0.0.2"]

    assert tuple(by_address) == ("1.1.1.1", "10.0.0.2", "8.8.8.8")
    assert local.entity_id == EntityId("ip:10.0.0.2")
    assert local.address_classification == "private"
    assert (local.organization, local.hostname, local.location) == (
        "Local Lab",
        "sensor",
        "Rack 1",
    )
    assert (local.bytes_out, local.packets_out, local.out_peers) == (150, 2, 2)
    assert local.out_ports == (53, 443)
    assert (local.bytes_in, local.packets_in, local.in_peers) == (200, 1, 1)
    assert local.in_ports == (50000,)
    assert local.protocols == ("TCP", "UDP")
    assert "0.0.0.0" not in by_address


def test_profiler_is_order_independent_and_caps_sorted_ports():
    packets = tuple(
        _packet(
            float(port),
            "10.0.0.2",
            f"192.0.2.{port - 99}",
            destination_port=port,
        )
        for port in range(100, 125)
    )
    profiler = EndpointProfiler()

    forward = _profiles(packets, profiler=profiler)
    reversed_result = _profiles(tuple(reversed(packets)), profiler=profiler)

    assert forward == reversed_result
    local = next(draft for draft in forward if draft.ip_address == "10.0.0.2")
    assert local.out_peers == 25
    assert local.out_ports == tuple(range(100, 120))


def test_timing_gate_and_capture_boundaries_preserve_beacon_cadence():
    assert interval_timing_seconds(((0, 1, 2, 3, 4),)) == (None, None)
    assert interval_timing_seconds(((0, 1, 2, 3, 4, 5),)) == (1.0, 0.0)
    assert endpoint_timing([[0, 1000, 2000, 3000, 4000, 5000]]) == (1.0, 0.0)

    first = CaptureId("cap_first")
    second = CaptureId("cap_second")
    packets = tuple(
        _packet(
            offset,
            "10.0.0.2",
            "8.8.8.8",
            capture_id=capture_id,
        )
        for capture_id, base in ((first, 0.0), (second, 86_400.0))
        for offset in (base, base + 10.0, base + 20.0, base + 30.0)
    )

    local = next(draft for draft in _profiles(packets) if draft.ip_address == "10.0.0.2")

    assert local.interval_mean == pytest.approx(10.0)
    assert local.interval_cv == pytest.approx(0.0)
    assert local.timing_direction is TimingDirection.OUTBOUND


def test_more_regular_qualifying_direction_supplies_endpoint_timing():
    outbound = tuple(_packet(seconds, "10.0.0.2", "8.8.8.8") for seconds in (0, 10, 20, 30, 40, 50))
    inbound = tuple(
        _packet(seconds, "1.1.1.1", "10.0.0.2") for seconds in (100, 101, 104, 110, 120, 135)
    )

    local = next(draft for draft in _profiles(outbound + inbound) if draft.ip_address == "10.0.0.2")

    assert local.interval_mean == pytest.approx(10.0)
    assert local.interval_cv == pytest.approx(0.0)
    assert local.timing_direction is TimingDirection.OUTBOUND


def test_selected_inbound_timing_direction_and_sparse_provenance_are_explicit():
    outbound = tuple(_packet(seconds, "10.0.0.2", "8.8.8.8") for seconds in (0, 1, 4, 10, 20, 35))
    inbound = tuple(
        _packet(seconds, "1.1.1.1", "10.0.0.2") for seconds in (100, 110, 120, 130, 140, 150)
    )

    local = next(draft for draft in _profiles(outbound + inbound) if draft.ip_address == "10.0.0.2")
    sparse = next(draft for draft in _profiles(outbound[:1]) if draft.ip_address == "10.0.0.2")

    assert local.interval_mean == pytest.approx(10.0)
    assert local.interval_cv == pytest.approx(0.0)
    assert local.timing_direction is TimingDirection.INBOUND
    assert sparse.interval_mean is None
    assert sparse.interval_cv is None
    assert sparse.timing_direction is None


def test_profiler_packet_gate_must_match_representation_metadata():
    with pytest.raises(UnsupportedNumericFeatureSetError, match="packet gate must match"):
        EndpointProfiler(minimum_timing_packets=7).profile(
            (),
            entity_definition=ENDPOINT_IP_V1,
            observation_window=_window(),
            numeric_feature_set=ENDPOINT_NUMERIC_FEATURE_SET_V1,
        )


def test_profile_draft_rejects_identity_drift_and_profiler_bounds():
    draft = _profiles((_packet(0, "10.0.0.2", "8.8.8.8"),))[0]

    with pytest.raises(ValueError, match="entity_id must match"):
        replace(draft, entity_id=EntityId("ip:1.1.1.1"))
    with pytest.raises(ValueError, match="direction must accompany"):
        replace(draft, timing_direction=TimingDirection.OUTBOUND)
    with pytest.raises(ValueError, match="at least two"):
        EndpointProfiler(minimum_timing_packets=1)
    with pytest.raises(ValueError, match="ports must be positive"):
        EndpointProfiler(maximum_ports=0)


def test_negative_packet_size_is_confined_to_explicit_legacy_compatibility():
    valid = _packet(0, "10.0.0.2", "8.8.8.8", size=2)
    malformed = SimpleNamespace(
        capture_id=valid.capture_id,
        observed_at=valid.observed_at,
        protocol=valid.protocol,
        size_bytes=-1,
        source_ip=valid.source_ip,
        destination_ip=valid.destination_ip,
        source_port=valid.source_port,
        destination_port=valid.destination_port,
    )

    with pytest.raises(ValueError, match="size cannot be negative"):
        _profiles((malformed, valid))

    drafts = _profiles(
        (malformed, valid),
        profiler=EndpointProfiler(allow_legacy_negative_packet_sizes=True),
    )

    local = next(draft for draft in drafts if draft.ip_address == "10.0.0.2")
    assert local.bytes_out == 1


def test_profile_result_retains_exact_entity_and_single_capture_window():
    packet = _packet(5, "10.0.0.2", "8.8.8.8")
    window = _window(
        packet,
        started_at=NOW,
        ended_at=NOW + timedelta(seconds=10),
    )

    result = EndpointProfiler().profile(
        (packet,),
        entity_definition=ENDPOINT_IP_V1,
        observation_window=window,
        numeric_feature_set=ENDPOINT_NUMERIC_FEATURE_SET_V1,
    )

    assert result.entity_definition is ENDPOINT_IP_V1
    assert result.observation_window is window
    assert result.numeric_feature_set is ENDPOINT_NUMERIC_FEATURE_SET_V1
    assert result.source_packet_count == 1
    assert len(result.profiles) == 2


def test_pooled_window_accepts_declared_capture_set_and_empty_window_is_valid():
    second_capture = CaptureId("cap_profile_second")
    packets = (
        _packet(0, "10.0.0.2", "8.8.8.8"),
        _packet(1, "10.0.0.2", "1.1.1.1", capture_id=second_capture),
    )
    window = _window(*packets, capture_ids=(CAPTURE, second_capture))

    pooled = EndpointProfiler().profile(
        packets,
        entity_definition=ENDPOINT_IP_V1,
        observation_window=window,
        numeric_feature_set=ENDPOINT_NUMERIC_FEATURE_SET_V1,
    )
    empty = EndpointProfiler().profile(
        (),
        entity_definition=ENDPOINT_IP_V1,
        observation_window=ObservationWindow(capture_ids=(CAPTURE,)),
        numeric_feature_set=ENDPOINT_NUMERIC_FEATURE_SET_V1,
    )

    assert pooled.observation_window.capture_ids == (CAPTURE, second_capture)
    assert pooled.source_packet_count == 2
    assert empty.source_packet_count == 0
    assert empty.profiles == ()


@pytest.mark.parametrize(
    ("window", "message"),
    (
        (
            ObservationWindow(capture_ids=(CaptureId("cap_other"),)),
            "outside the observation window",
        ),
        (
            ObservationWindow(capture_ids=(CAPTURE,), started_at=NOW + timedelta(seconds=1)),
            "precedes the observation window",
        ),
        (
            ObservationWindow(capture_ids=(CAPTURE,), ended_at=NOW - timedelta(seconds=1)),
            "follows the observation window",
        ),
    ),
)
def test_profiler_rejects_packet_evidence_outside_declared_window(window, message):
    with pytest.raises(ProfilingWindowError, match=message):
        EndpointProfiler().profile(
            (_packet(0, "10.0.0.2", "8.8.8.8"),),
            entity_definition=ENDPOINT_IP_V1,
            observation_window=window,
            numeric_feature_set=ENDPOINT_NUMERIC_FEATURE_SET_V1,
        )


@pytest.mark.parametrize(
    "entity",
    (
        EntityDefinition(entity_type=EntityType.HOST_DESTINATION, version="1"),
        EntityDefinition(entity_type=EntityType.ENDPOINT_IP, version="2"),
    ),
)
def test_profiler_rejects_unsupported_entity_type_or_version(entity):
    with pytest.raises(UnsupportedEntityDefinitionError, match="endpoint_ip.*version='1'"):
        EndpointProfiler().profile(
            (),
            entity_definition=entity,
            observation_window=ObservationWindow(capture_ids=(CAPTURE,)),
            numeric_feature_set=ENDPOINT_NUMERIC_FEATURE_SET_V1,
        )


def test_profiler_rejects_unsupported_numeric_feature_set_version():
    unsupported = replace(ENDPOINT_NUMERIC_FEATURE_SET_V1, version="2")

    with pytest.raises(UnsupportedNumericFeatureSetError, match="endpoint_profile_numeric.*'1'"):
        EndpointProfiler().profile(
            (),
            entity_definition=ENDPOINT_IP_V1,
            observation_window=ObservationWindow(capture_ids=(CAPTURE,)),
            numeric_feature_set=unsupported,
        )


def test_legacy_session_adapter_declares_single_pooled_and_unscoped_windows():
    frame = pd.DataFrame({"capture_id": ["cap_first", "cap_second"]})

    single = profile_observation_window("cap_second", ["cap_first", "cap_second"], frame)
    pooled = profile_observation_window(None, ["cap_first", "cap_second"], frame)
    unscoped = profile_observation_window(None, [], pd.DataFrame())

    assert single.capture_ids == (CaptureId("cap_second"),)
    assert pooled.capture_ids == (CaptureId("cap_first"), CaptureId("cap_second"))
    assert unscoped.capture_ids == (CaptureId("legacy-unscoped"),)


def test_entity_and_window_declarations_reject_ambiguous_identity():
    with pytest.raises(ValueError, match="version cannot be empty"):
        EntityDefinition(entity_type=EntityType.ENDPOINT_IP, version=" ")
    with pytest.raises(ValueError, match="capture IDs must be unique"):
        ObservationWindow(capture_ids=(CAPTURE, CAPTURE))
    with pytest.raises(ValueError, match="filters cannot be empty"):
        ObservationWindow(capture_ids=(CAPTURE,), filters=("tcp", " "))


def test_legacy_dataframe_adapter_preserves_profile_shape():
    packets = pd.DataFrame(
        [
            {
                "src_ip": "10.0.0.2",
                "dst_ip": "8.8.8.8",
                "src_port": 50000,
                "dst_port": 443,
                "size": 100,
                "protocol": "TCP",
                "ts_ms": 0.0,
                "capture_id": "fixture",
            },
            {
                "src_ip": "8.8.8.8",
                "dst_ip": "10.0.0.2",
                "src_port": 443,
                "dst_port": 50000,
                "size": 200,
                "protocol": "TCP",
                "ts_ms": 100.0,
                "capture_id": "fixture",
            },
        ]
    )

    profiles = build_endpoint_profiles(
        packets,
        {
            "8.8.8.8": {
                "ip_address": "8.8.8.8",
                "org": "Google LLC",
                "hostname": "dns.google",
                "location": "US",
            }
        },
    )

    assert profiles == [
        {
            "ip_address": "10.0.0.2",
            "endpoint_type": "private",
            "org": None,
            "hostname": None,
            "location": None,
            "bytes_out": 100,
            "packets_out": 1,
            "out_peers": 1,
            "out_ports": [443],
            "bytes_in": 200,
            "packets_in": 1,
            "in_peers": 1,
            "in_ports": [50000],
            "protocols": ["TCP"],
            "interval_mean": None,
            "interval_cv": None,
        },
        {
            "ip_address": "8.8.8.8",
            "endpoint_type": "public",
            "org": "Google LLC",
            "hostname": "dns.google",
            "location": "US",
            "bytes_out": 200,
            "packets_out": 1,
            "out_peers": 1,
            "out_ports": [50000],
            "bytes_in": 100,
            "packets_in": 1,
            "in_peers": 1,
            "in_ports": [443],
            "protocols": ["TCP"],
            "interval_mean": None,
            "interval_cv": None,
        },
    ]
