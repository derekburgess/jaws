"""Compatibility checks for repository-backed MCP read tools."""

import importlib.util
import sys
from datetime import UTC, datetime
from types import ModuleType, SimpleNamespace

if importlib.util.find_spec("mcp") is None:
    mcp_package = ModuleType("mcp")
    mcp_server = ModuleType("mcp.server")

    class _MCPServer:
        def __init__(self, *_args, **_kwargs):
            pass

        def tool(self, **_kwargs):
            return lambda function: function

    mcp_server.MCPServer = _MCPServer
    mcp_package.server = mcp_server
    sys.modules["mcp"] = mcp_package
    sys.modules["mcp.server"] = mcp_server

from jaws.domain import (
    CaptureId,
    CaptureRecord,
    CaptureSourceKind,
    CaptureState,
    EndpointInspection,
    EndpointPacketSample,
    EndpointPeerTraffic,
    EndpointProfile,
    EntityId,
    EntityMetadata,
    ObservationScopeId,
    OutlierStatus,
    ProfileIdentity,
    ProfileScopeSummary,
)
from jaws_mcp import server


class _Catalog:
    def __init__(self, records):
        self.records = records

    def list_all(self):
        return self.records


class _Profiles:
    def __init__(self, summaries):
        self.summaries = summaries

    def list_scopes(self):
        return self.summaries


class _Inspection:
    def __init__(self, profile, inspection):
        self.profile = profile
        self.inspection = inspection
        self.recent_call = None
        self.inspect_call = None

    def recent_profiles(self, *, computed_after, limit):
        self.recent_call = (computed_after, limit)
        return (self.profile,)

    def inspect(self, entity_id, *, peer_limit, packet_limit, history_limit):
        self.inspect_call = (entity_id, peer_limit, packet_limit, history_limit)
        return self.inspection


def _fixture_bundle():
    observed_at = datetime(2026, 8, 12, 12, tzinfo=UTC)
    capture_id = CaptureId("cap_mcp_fixture")
    capture = CaptureRecord(
        capture_id=capture_id,
        source_kind=CaptureSourceKind.LIVE_INTERFACE,
        source_name="eth0",
        state=CaptureState.RUNNING,
        registered_at=observed_at,
        started_at=observed_at,
        packet_count=4,
    )
    profile = EndpointProfile(
        identity=ProfileIdentity(
            entity_id=EntityId("ip:198.51.100.20"),
            scope_id=ObservationScopeId("scope_cap_mcp_fixture"),
            representation_id="fixture",
            representation_version="1",
            model_id="fixture-model",
            model_revision="fixture-revision",
        ),
        legacy_scope=capture_id.value,
        computed_at=observed_at,
        address_classification="public",
        organization="Example Networks",
        hostname="target.example",
        location="Example City",
        bytes_out=300,
        packets_out=3,
        out_peers=1,
        out_ports=(443,),
        bytes_in=200,
        packets_in=2,
        in_peers=1,
        in_ports=(50000,),
        protocols=("TCP",),
        interval_mean=0.5,
        interval_cv=0.1,
        outlier=OutlierStatus.OUTLIER,
    )
    peer = EndpointPeerTraffic(
        peer=EntityMetadata(
            entity_id=EntityId("ip:192.0.2.10"),
            ip_address="192.0.2.10",
            organization="Local Network",
        ),
        bytes_out=300,
        packets_out=3,
        bytes_in=200,
        packets_in=2,
        protocols=("TCP",),
        service_ports=(443,),
        ephemeral_ports=2,
    )
    packet = EndpointPacketSample(
        source_ip="198.51.100.20",
        source_port=443,
        destination_ip="192.0.2.10",
        destination_port=50000,
        protocol="TCP",
        size_bytes=300,
        observed_at=observed_at,
    )
    detail = EndpointInspection(
        entity_id=profile.identity.entity_id,
        profile=profile,
        total_packets=5,
        total_peers=1,
        history=(profile,),
        peers=(peer,),
        packets=(packet,),
    )
    inspection = _Inspection(profile, detail)
    bundle = SimpleNamespace(
        captures=_Catalog((capture,)),
        profiles=_Profiles(
            (
                ProfileScopeSummary(
                    scope_id=profile.identity.scope_id,
                    legacy_scope=capture_id.value,
                    computed_at=observed_at,
                    profile_count=1,
                ),
            )
        ),
        inspection=inspection,
    )
    return bundle, inspection


def test_mcp_catalog_and_overview_preserve_legacy_payloads(monkeypatch):
    bundle, inspection = _fixture_bundle()
    monkeypatch.setattr(server, "_repositories", lambda: bundle)

    catalog = server.list_captures()
    assert catalog == {
        "ok": True,
        "captures": [
            {
                "capture_id": "cap_mcp_fixture",
                "source": "eth0",
                "started": "2026-08-12T12:00:00.000000Z",
                "packets": 4,
            }
        ],
        "count": 1,
        "profiled_session": "cap_mcp_fixture",
        "profiled_sessions": [
            {
                "session": "cap_mcp_fixture",
                "endpoints": 1,
                "computed": "2026-08-12T12:00:00.000000Z",
            }
        ],
        "baseline_sessions": 0,
    }

    overview = server.fetch_traffic(duration_minutes=30, limit=1)
    assert overview["ok"] is True
    assert overview["count"] == 1
    assert overview["duration_minutes"] == 30
    assert overview["endpoints"][0] == {
        "ip_address": "198.51.100.20",
        "endpoint_type": "public",
        "capture_id": "cap_mcp_fixture",
        "org": "Example Networks",
        "hostname": "target.example",
        "location": "Example City",
        "bytes_out": 300,
        "packets_out": 3,
        "out_peers": 1,
        "out_ports": [443],
        "bytes_in": 200,
        "packets_in": 2,
        "in_peers": 1,
        "in_ports": [50000],
        "protocols": ["TCP"],
        "interval_mean": 0.5,
        "interval_cv": 0.1,
        "outlier": True,
        "timestamp": "2026-08-12T12:00:00.000000Z",
        "cloud_hosted": False,
    }
    assert inspection.recent_call is not None
    assert inspection.recent_call[1] == 1


def test_mcp_endpoint_drilldown_preserves_limits_directions_and_counts(monkeypatch):
    bundle, inspection = _fixture_bundle()
    monkeypatch.setattr(server, "_repositories", lambda: bundle)

    payload = server.inspect_endpoint(
        "198.51.100.20", peer_limit=7, packet_limit=3, history_limit=4
    )

    assert payload["ok"] is True
    assert payload["found"] is True
    assert payload["totals"] == {"packets": 5, "peers": 1, "scope": "all_sessions"}
    assert payload["sessions_seen"] == 1
    assert payload["profile"]["cloud_hosted"] is False
    assert payload["history"][0]["capture_id"] == "cap_mcp_fixture"
    assert payload["peers"] == [
        {
            "peer_ip": "192.0.2.10",
            "peer_org": "Local Network",
            "peer_hostname": None,
            "peer_location": None,
            "bytes_out": 300,
            "packets_out": 3,
            "bytes_in": 200,
            "packets_in": 2,
            "bytes_total": 500,
            "protocols": ["TCP"],
            "service_ports": [443],
            "ephemeral_ports": 2,
        }
    ]
    assert payload["packets"] == [
        {
            "src_ip": "198.51.100.20",
            "src_port": 443,
            "dst_ip": "192.0.2.10",
            "dst_port": 50000,
            "protocol": "TCP",
            "size": 300,
            "timestamp": "2026-08-12T12:00:00.000000Z",
        }
    ]
    assert inspection.inspect_call == (EntityId("ip:198.51.100.20"), 7, 3, 4)
