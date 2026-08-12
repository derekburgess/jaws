"""Legacy capture-adapter tests for the new identity and lifecycle contract."""

from __future__ import annotations

import hashlib
import sys
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import SimpleNamespace

from jaws import jaws_capture as capture
from jaws.domain import CaptureId, ObservationWindow
from jaws.ports import (
    FrozenClock,
    InMemoryCaptureRepository,
    InMemoryPacketRepository,
    SequenceIdGenerator,
)


@dataclass
class FakeResult:
    def consume(self) -> None:
        return None


@dataclass
class RecordingDriver:
    calls: list[tuple[str, dict[str, object]]] = field(default_factory=list)
    closed: bool = False

    def session(self, *, database: str) -> RecordingDriver:
        assert database == "fixtures"
        return self

    def __enter__(self) -> RecordingDriver:
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        return None

    def run(self, query: str, parameters=None, **kwargs) -> FakeResult:
        values = dict(parameters or kwargs)
        self.calls.append((" ".join(query.split()), values))
        return FakeResult()

    def execute_write(self, work):
        return work(self)

    def close(self) -> None:
        self.closed = True


class SilentReporter:
    def __init__(self):
        self.errors = []

    def info(self, title, message):
        return None

    def error(self, title, message):
        self.errors.append((title, message))

    def result(self, obj, summary=None):
        return None

    @contextmanager
    def activity(self, render):
        yield lambda: None


class InterruptingLiveCapture:
    def __init__(self, interface):
        self.interface = interface

    def apply_on_packets(self, callback, timeout):
        raise KeyboardInterrupt

    def close(self):
        return None


class PartialFileCapture:
    def __init__(self, path):
        self.path = path

    def __iter__(self):
        yield object()
        raise RuntimeError("fixture import failure")

    def close(self):
        return None


def _runtime(monkeypatch, driver, reporter):
    now = datetime(2026, 8, 10, 15, 30, tzinfo=UTC)
    monkeypatch.setattr(capture, "Reporter", lambda: reporter)
    monkeypatch.setattr(capture, "SystemClock", lambda: FrozenClock(now))
    monkeypatch.setattr(
        capture,
        "UuidCaptureIdGenerator",
        lambda: SequenceIdGenerator((CaptureId("cap_fixture"),)),
    )
    monkeypatch.setattr(capture, "dbms_connection", lambda database, reporter: driver)
    monkeypatch.setattr(capture, "initialize_schema", lambda *args: None)
    monkeypatch.setattr(capture, "get_local_ip", lambda: "10.0.0.2")
    monkeypatch.setattr(capture, "capture_tool_versions", lambda: {"jaws": "2.0.0"})
    captures = InMemoryCaptureRepository()
    repositories = SimpleNamespace(
        captures=captures,
        packets=InMemoryPacketRepository(captures),
    )
    monkeypatch.setattr(
        capture,
        "Neo4jRepositories",
        SimpleNamespace(connect=lambda driver, database: repositories),
    )
    return repositories


def test_interrupted_live_capture_is_finalized_as_cancelled(monkeypatch):
    driver = RecordingDriver()
    reporter = SilentReporter()
    repositories = _runtime(monkeypatch, driver, reporter)
    monkeypatch.setattr(capture, "list_interfaces", lambda: ["eth0"])
    monkeypatch.setattr(
        capture,
        "require_module",
        lambda *args: SimpleNamespace(LiveCapture=InterruptingLiveCapture),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["jaws-capture", "--interface", "eth0", "--database", "fixtures"],
    )

    capture.main()

    finalization = repositories.captures.get(CaptureId("cap_fixture"))
    assert finalization.state.value == "cancelled"
    assert finalization.packet_count == 0
    assert finalization.failure_code == "capture_cancelled"
    assert reporter.errors == [("CANCELLED", "Capture cancelled.")]
    assert driver.closed


def test_failed_import_flushes_available_evidence_and_is_partial(monkeypatch, tmp_path):
    capture_path = tmp_path / "fixture.pcap"
    capture_path.write_bytes(b"fixture evidence")
    driver = RecordingDriver()
    reporter = SilentReporter()
    repositories = _runtime(monkeypatch, driver, reporter)
    monkeypatch.setattr(
        capture,
        "require_module",
        lambda *args: SimpleNamespace(FileCapture=PartialFileCapture),
    )
    monkeypatch.setattr(
        capture,
        "process_packet",
        lambda packet: (
            {
                "protocol": "TCP",
                "src_ip_address": "10.0.0.2",
                "dst_ip_address": "8.8.8.8",
                "src_port": 12345,
                "dst_port": 443,
                "size": 64,
                "payload": None,
                "timestamp": "2026-08-10T15:30:00+00:00",
            },
            "fixture packet",
        ),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["jaws-capture", "--file", str(capture_path), "--database", "fixtures"],
    )

    capture.main()

    finalization = repositories.captures.get(CaptureId("cap_fixture"))
    assert str(finalization.content_digest) == hashlib.sha256(b"fixture evidence").hexdigest()
    assert finalization.perspective is None
    assert finalization.state.value == "partial"
    assert finalization.packet_count == 1
    assert finalization.failure_code == "RuntimeError"
    packets = repositories.packets.read(ObservationWindow(capture_ids=(CaptureId("cap_fixture"),)))
    assert len(packets) == 1
    assert reporter.errors == [("ERROR", "fixture import failure")]
    assert driver.closed


def test_file_sha256_reads_content_in_bounded_chunks(tmp_path):
    path = tmp_path / "evidence.pcap"
    content = b"a" * (1024 * 1024 + 7)
    path.write_bytes(content)
    assert capture.file_sha256(path) == hashlib.sha256(content).hexdigest()


def test_historical_profiles_order_by_capture_time_not_opaque_identity():
    from jaws.storage.neo4j_profile_repositories import _READ_HISTORY

    assert "historical_capture.STARTED" in _READ_HISTORY
    assert "< target_started" in _READ_HISTORY
    assert "CAPTURE_ID < $scope" not in _READ_HISTORY
