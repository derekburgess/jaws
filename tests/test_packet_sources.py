"""PyShark adapter parsing policy and bounded source behavior."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError
from types import SimpleNamespace

import pytest

from jaws.adapters import (
    LivePacketSource,
    PcapPacketSource,
    PySharkPacketParser,
    capture_tool_versions,
    file_sha256,
)


class Packet:
    def __init__(
        self,
        *layers,
        highest_layer: str = "",
        sniff_timestamp: str | None = "1787169600.25",
        sniff_time: datetime | None = None,
        size: int = 96,
    ) -> None:
        self.layers = layers
        self.highest_layer = highest_layer
        self.sniff_timestamp = sniff_timestamp
        self.sniff_time = sniff_time
        self.size = size
        for layer in layers:
            name = layer.layer_name
            if not hasattr(self, name):
                setattr(self, name, layer)

    def __len__(self) -> int:
        return self.size

    def get_multiple_layers(self, name: str):
        return tuple(layer for layer in self.layers if layer.layer_name == name)


def layer(name: str, **fields):
    return SimpleNamespace(layer_name=name, **fields)


def test_parser_emits_ipv4_tcp_with_epoch_timestamp_ports_and_payload():
    parser = PySharkPacketParser()
    packet = Packet(
        layer("eth"),
        layer("ip", src="192.0.2.10", dst="198.51.100.20"),
        layer("tcp", srcport="50000", dstport="443", payload="00:01"),
        highest_layer="TLS",
        size=128,
    )

    observation = parser.parse(packet)

    assert observation is not None
    assert observation.observed_at == datetime(2026, 8, 19, 20, 0, 0, 250000, tzinfo=UTC)
    assert observation.protocol == "TLS"
    assert observation.source_ip == "192.0.2.10"
    assert observation.destination_ip == "198.51.100.20"
    assert observation.source_port == 50000
    assert observation.destination_port == 443
    assert observation.payload == "00:01"
    assert parser.stats.seen == parser.stats.emitted == 1


def test_parser_supports_ipv6_udp_and_aware_frame_time():
    parser = PySharkPacketParser()
    captured_at = datetime(2026, 8, 19, 20, tzinfo=UTC)
    packet = Packet(
        layer("ipv6", src="2001:db8::1", dst="2001:db8::2"),
        layer("udp", srcport="53", dstport="53000"),
        highest_layer="DNS",
        sniff_timestamp=None,
        sniff_time=captured_at,
    )

    observation = parser.parse(packet)

    assert observation is not None
    assert observation.observed_at == captured_at
    assert observation.source_ip == "2001:db8::1"
    assert observation.destination_ip == "2001:db8::2"
    assert observation.source_port == 53
    assert observation.destination_port == 53000


def test_vlan_and_tunnel_use_outermost_decoded_ip_pair():
    parser = PySharkPacketParser()
    packet = Packet(
        layer("eth"),
        layer("vlan"),
        layer("ip", src="192.0.2.1", dst="192.0.2.2"),
        layer("gre"),
        layer("ip", src="10.0.0.1", dst="10.0.0.2"),
        layer("icmp"),
        highest_layer="ICMP",
    )

    observation = parser.parse(packet)

    assert observation is not None
    assert observation.source_ip == "192.0.2.1"
    assert observation.destination_ip == "192.0.2.2"
    assert observation.source_port is None
    assert observation.destination_port is None


def test_non_ip_and_malformed_ip_are_skipped_without_synthetic_addresses():
    parser = PySharkPacketParser()
    non_ip = Packet(layer("eth"), layer("arp"), highest_layer="ARP")
    naive_time = Packet(
        layer("ip", src="192.0.2.1", dst="198.51.100.1"),
        highest_layer="IP",
        sniff_timestamp=None,
        sniff_time=datetime(2026, 8, 19, 20),
    )

    assert parser.parse(non_ip) is None
    assert parser.parse(naive_time) is None
    assert parser.stats.seen == 2
    assert parser.stats.skipped_non_ip == 1
    assert parser.stats.skipped_malformed == 1
    assert parser.stats.emitted == 0


@dataclass
class FakeFileCapture:
    packets: tuple[Packet, ...]
    closed: bool = False

    def __iter__(self):
        return iter(self.packets)

    def close(self) -> None:
        self.closed = True


def test_pcap_source_streams_supported_packets_and_closes_capture():
    capture = FakeFileCapture(
        (
            Packet(layer("arp"), highest_layer="ARP"),
            Packet(
                layer("ip", src="192.0.2.1", dst="198.51.100.1"),
                layer("udp", srcport="123", dstport="123"),
                highest_layer="NTP",
            ),
        )
    )
    calls = []

    def file_capture(path: str, **parameters):
        calls.append((path, parameters))
        return capture

    observed = []
    source = PcapPacketSource(
        SimpleNamespace(FileCapture=file_capture),
        "fixture.pcap",
        display_filter="ip",
        observer=lambda packet, summary: observed.append((packet, summary)),
    )

    packets = tuple(source.packets())

    assert len(packets) == 1
    assert calls == [("fixture.pcap", {"keep_packets": False, "display_filter": "ip"})]
    assert capture.closed
    assert len(observed) == 1
    assert "192.0.2.1:123" in observed[0][1]


@dataclass
class FakeLiveCapture:
    packets: tuple[Packet, ...]
    error: BaseException | None = None
    closed: bool = False
    timeout: int | None = None

    def apply_on_packets(self, callback, timeout: int) -> None:
        self.timeout = timeout
        for packet in self.packets:
            callback(packet)
        if self.error is not None:
            raise self.error

    def close(self) -> None:
        self.closed = True


def test_live_source_bridges_callback_capture_through_bounded_queue():
    capture = FakeLiveCapture(
        (
            Packet(layer("arp"), highest_layer="ARP"),
            Packet(
                layer("ipv6", src="2001:db8::1", dst="2001:db8::2"),
                layer("icmpv6"),
                highest_layer="ICMPV6",
            ),
        )
    )
    calls = []

    def live_capture(**parameters):
        calls.append(parameters)
        return capture

    source = LivePacketSource(
        SimpleNamespace(LiveCapture=live_capture),
        "eth0",
        30,
        capture_filter="ip or ip6",
        display_filter="icmpv6",
        queue_size=1,
    )

    packets = tuple(source.packets())

    assert len(packets) == 1
    assert packets[0].protocol == "ICMPV6"
    assert calls == [
        {
            "interface": "eth0",
            "bpf_filter": "ip or ip6",
            "display_filter": "icmpv6",
        }
    ]
    assert capture.timeout == 30
    assert capture.closed


def test_live_source_propagates_capture_interrupt_and_still_closes():
    capture = FakeLiveCapture((), KeyboardInterrupt())
    source = LivePacketSource(
        SimpleNamespace(LiveCapture=lambda **parameters: capture),
        "eth0",
        10,
    )

    with pytest.raises(KeyboardInterrupt):
        tuple(source.packets())

    assert capture.closed


def test_file_hash_and_tool_versions_are_bounded_explicit_provenance(tmp_path):
    path = tmp_path / "fixture.pcap"
    content = b"a" * (1024 * 1024 + 7)
    path.write_bytes(content)

    class Completed:
        stdout = "TShark (Wireshark) 4.4.0\nCopyright"

    def package_version(name: str) -> str:
        if name == "JAWS":
            return "2.0.0"
        raise PackageNotFoundError(name)

    versions = capture_tool_versions(
        package_version=package_version,
        runner=lambda *args, **kwargs: Completed(),
    )

    assert file_sha256(path) == hashlib.sha256(content).hexdigest()
    assert versions == {
        "jaws": "2.0.0",
        "pyshark": "unknown",
        "tshark": "TShark (Wireshark) 4.4.0",
    }
