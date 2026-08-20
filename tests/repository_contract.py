"""Shared behavioral contract for capture and packet repository implementations."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from jaws.domain import (
    CaptureId,
    CaptureRecord,
    CaptureSourceKind,
    CaptureSourceMetadata,
    CaptureState,
    EntityId,
    ObservationWindow,
    PacketRecord,
)
from jaws.ports import (
    CaptureNotFoundError,
    CaptureStateConflictError,
    DuplicateCaptureError,
    InactiveCaptureError,
)


def assert_capture_and_packet_repository_contract(repositories):
    """Exercise the same lifecycle, catalog, batch, and scoped-read behavior."""

    captured_at = datetime(2026, 8, 11, 12, tzinfo=UTC)
    first_id = CaptureId("cap_repository_fixture_first")
    second_id = CaptureId("cap_repository_fixture_second")
    missing_id = CaptureId("cap_repository_fixture_missing")
    first = CaptureRecord(
        capture_id=first_id,
        source_kind=CaptureSourceKind.LIVE_INTERFACE,
        source_name="eth0",
        state=CaptureState.REGISTERED,
        registered_at=captured_at,
        legacy_capture_id="20260811T120000Z",
        perspective=EntityId("ip:192.0.2.10"),
        tool_versions={"jaws": "2.0.0"},
    )
    second = CaptureRecord(
        capture_id=second_id,
        source_kind=CaptureSourceKind.PCAP_FILE,
        source_name="fixture.pcap",
        state=CaptureState.REGISTERED,
        registered_at=captured_at + timedelta(seconds=1),
        legacy_capture_id="20260811T120000Z",
        source_metadata=CaptureSourceMetadata(
            "fixture.pcap", 2048, "/fixtures/fixture.pcap", False
        ),
        tool_versions={"jaws": "2.0.0"},
    ).transition(CaptureState.IMPORTING, captured_at + timedelta(seconds=1))

    repositories.captures.add(first)
    with pytest.raises(DuplicateCaptureError):
        repositories.captures.add(first)
    assert repositories.captures.get(first_id) == first
    assert repositories.captures.get(missing_id) is None

    running = first.transition(CaptureState.RUNNING, captured_at)
    repositories.captures.transition(running, expected_state=CaptureState.REGISTERED)
    with pytest.raises(CaptureStateConflictError):
        repositories.captures.transition(running, expected_state=CaptureState.REGISTERED)
    with pytest.raises(CaptureNotFoundError):
        repositories.captures.transition(
            replace(running, capture_id=missing_id),
            expected_state=CaptureState.RUNNING,
        )

    repositories.captures.add(second)
    assert repositories.captures.find_by_legacy_id("20260811T120000Z") == (running, second)
    assert repositories.captures.list_all() == (running, second)

    first_early = PacketRecord(
        capture_id=first_id,
        observed_at=captured_at + timedelta(milliseconds=100),
        protocol="TCP",
        size_bytes=64,
        source_ip="192.0.2.10",
        destination_ip="198.51.100.20",
        source_port=50000,
        destination_port=443,
        payload="00:01",
    )
    first_late = replace(first_early, observed_at=captured_at + timedelta(milliseconds=300))
    second_packet = replace(
        first_early,
        capture_id=second_id,
        observed_at=captured_at + timedelta(milliseconds=200),
        source_port=None,
        destination_port=None,
        payload=None,
    )
    assert repositories.packets.append(first_id, ()) == 0
    assert repositories.packets.append(first_id, (first_late, first_early)) == 2
    assert repositories.packets.append(second_id, (second_packet,)) == 1
    assert repositories.packets.read_all() == (first_early, second_packet, first_late)
    with pytest.raises(ValueError):
        repositories.packets.append(first_id, (first_early, second_packet))
    assert repositories.packets.read(ObservationWindow(capture_ids=(first_id,))) == (
        first_early,
        first_late,
    )
    with pytest.raises(CaptureNotFoundError):
        repositories.packets.append(missing_id, (replace(first_early, capture_id=missing_id),))

    window = ObservationWindow(
        capture_ids=(second_id, first_id),
        started_at=captured_at + timedelta(milliseconds=150),
        ended_at=captured_at + timedelta(milliseconds=350),
    )
    assert repositories.packets.read(window) == (second_packet, first_late)

    altered = replace(
        running.transition(
            CaptureState.COMPLETE,
            captured_at + timedelta(seconds=2),
            packet_count=2,
        ),
        source_name="rewritten-interface",
    )
    with pytest.raises(ValueError, match="metadata cannot change"):
        repositories.captures.transition(altered, expected_state=CaptureState.RUNNING)

    complete = running.transition(
        CaptureState.COMPLETE,
        captured_at + timedelta(seconds=2),
        packet_count=2,
    )
    repositories.captures.transition(complete, expected_state=CaptureState.RUNNING)
    assert repositories.captures.get(first_id) == complete
    with pytest.raises(InactiveCaptureError):
        repositories.packets.append(first_id, (first_early,))
