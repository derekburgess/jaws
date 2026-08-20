"""Pure ingest-service lifecycle, provenance, batching, and cancellation tests."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import pytest

from jaws.domain import (
    CanonicalDigest,
    CaptureId,
    CaptureSourceKind,
    CaptureSourceMetadata,
    CaptureSpec,
    CaptureState,
    EntityId,
    ObservationWindow,
    PacketObservation,
    PacketRecord,
)
from jaws.ports import (
    FrozenClock,
    InMemoryCaptureRepository,
    InMemoryPacketRepository,
    SequenceCancellationSignal,
    SequenceIdGenerator,
    SequencePacketSource,
)
from jaws.services import IngestService

NOW = datetime(2026, 8, 19, 20, tzinfo=UTC)


@dataclass(slots=True)
class RecordingPackets:
    repository: InMemoryPacketRepository
    batch_sizes: list[int] = field(default_factory=list)

    def append(self, capture_id: CaptureId, records: Sequence[PacketRecord]) -> int:
        self.batch_sizes.append(len(records))
        return self.repository.append(capture_id, records)


@dataclass(slots=True)
class FailingPackets(RecordingPackets):
    fail_on_batch: int = 2

    def append(self, capture_id: CaptureId, records: Sequence[PacketRecord]) -> int:
        if len(self.batch_sizes) + 1 >= self.fail_on_batch:
            self.batch_sizes.append(len(records))
            raise OSError("fixture storage unavailable")
        return RecordingPackets.append(self, capture_id, records)


@dataclass(frozen=True, slots=True)
class FailingSource:
    values: tuple[PacketObservation, ...]
    error: BaseException

    def packets(self) -> Iterator[PacketObservation]:
        yield from self.values
        raise self.error


def _observation(offset: int) -> PacketObservation:
    return PacketObservation(
        observed_at=NOW - timedelta(days=7) + timedelta(milliseconds=offset),
        protocol="TCP",
        size_bytes=64 + offset,
        source_ip="192.0.2.10",
        destination_ip="198.51.100.20",
        source_port=50000,
        destination_port=443,
    )


def _service(*, batch_size: int = 2):
    captures = InMemoryCaptureRepository()
    stored_packets = InMemoryPacketRepository(captures)
    packets = RecordingPackets(stored_packets)
    service = IngestService(
        captures=captures,
        packets=packets,
        clock=FrozenClock(NOW),
        capture_ids=SequenceIdGenerator((CaptureId("cap_ingest_fixture"),)),
        batch_size=batch_size,
    )
    return service, captures, stored_packets, packets


def _live_spec() -> CaptureSpec:
    return CaptureSpec(
        source_kind=CaptureSourceKind.LIVE_INTERFACE,
        source_name="eth0",
        perspective=EntityId("ip:192.0.2.10"),
        capture_filter="tcp port 443",
        tool_versions={"pyshark": "0.6", "tshark": "4.4.0"},
    )


def test_ingest_preserves_source_timestamps_and_assigns_one_capture_session():
    service, captures, stored_packets, packets = _service()
    observations = (_observation(30), _observation(10), _observation(20))

    result = service.ingest(_live_spec(), SequencePacketSource(observations))

    assert result.state is CaptureState.COMPLETE
    assert result.packet_count == 3
    assert result.perspective == EntityId("ip:192.0.2.10")
    assert result.capture_filter == "tcp port 443"
    assert result.tool_versions == {"pyshark": "0.6", "tshark": "4.4.0"}
    assert captures.get(result.capture_id) == result
    assert packets.batch_sizes == [2, 1]
    records = stored_packets.read(ObservationWindow(capture_ids=(result.capture_id,)))
    assert tuple(record.observed_at for record in records) == tuple(
        sorted(observation.observed_at for observation in observations)
    )
    assert {record.capture_id for record in records} == {result.capture_id}


def test_packet_record_keeps_its_existing_capture_first_constructor_shape():
    record = PacketRecord(
        CaptureId("cap_positional_compatibility"),
        NOW,
        "UDP",
        48,
        "192.0.2.1",
        "198.51.100.1",
    )

    assert record.capture_id == CaptureId("cap_positional_compatibility")
    assert record.observed_at == NOW


@pytest.mark.parametrize("perspective", [None, EntityId("ip:203.0.113.5")])
def test_pcap_perspective_is_explicit_and_never_inferred_from_runtime(perspective):
    service, _, _, _ = _service()
    digest = CanonicalDigest("a" * 64)
    spec = CaptureSpec(
        source_kind=CaptureSourceKind.PCAP_FILE,
        source_name="datasets/fixture.pcap",
        perspective=perspective,
        content_digest=digest,
        source_metadata=CaptureSourceMetadata("fixture.pcap", 4096, "datasets/fixture.pcap", False),
        tool_versions={"tshark": "4.4.0"},
    )

    result = service.ingest(spec, SequencePacketSource(()))

    assert result.state is CaptureState.COMPLETE
    assert result.packet_count == 0
    assert result.perspective == perspective
    assert result.content_digest == digest
    assert result.source_metadata == spec.source_metadata


def test_capture_spec_rejects_implicit_live_identity_and_unhashed_pcap():
    with pytest.raises(ValueError, match="explicit host perspective"):
        CaptureSpec(CaptureSourceKind.LIVE_INTERFACE, "eth0")
    with pytest.raises(ValueError, match="content SHA-256"):
        CaptureSpec(CaptureSourceKind.PCAP_FILE, "fixture.pcap")
    with pytest.raises(ValueError, match="file source metadata"):
        CaptureSpec(
            CaptureSourceKind.PCAP_FILE,
            "fixture.pcap",
            content_digest=CanonicalDigest("a" * 64),
        )
    with pytest.raises(ValueError, match="requires a PCAP-file"):
        CaptureSpec(
            CaptureSourceKind.LIVE_INTERFACE,
            "eth0",
            perspective=EntityId("ip:192.0.2.10"),
            source_metadata=CaptureSourceMetadata("fixture.pcap", 1),
        )
    with pytest.raises(ValueError, match="legacy-unknown"):
        CaptureSpec(CaptureSourceKind.LEGACY_UNKNOWN, "legacy")


def test_source_failure_flushes_available_evidence_and_finalizes_partial():
    service, captures, stored_packets, packets = _service()
    source = FailingSource(
        (_observation(1), _observation(2), _observation(3)), RuntimeError("boom")
    )

    with pytest.raises(RuntimeError, match="boom"):
        service.ingest(_live_spec(), source)

    result = captures.get(CaptureId("cap_ingest_fixture"))
    assert result is not None
    assert result.state is CaptureState.PARTIAL
    assert result.packet_count == 3
    assert result.failure_code == "RuntimeError"
    assert packets.batch_sizes == [2, 1]
    assert len(stored_packets.read(ObservationWindow(capture_ids=(result.capture_id,)))) == 3


def test_source_failure_before_evidence_is_distinct_from_clean_zero_packet_capture():
    service, captures, _, _ = _service()

    with pytest.raises(ValueError, match="unreadable fixture"):
        service.ingest(_live_spec(), FailingSource((), ValueError("unreadable fixture")))

    result = captures.get(CaptureId("cap_ingest_fixture"))
    assert result is not None
    assert result.state is CaptureState.FAILED
    assert result.packet_count == 0
    assert result.failure_code == "ValueError"


def test_batch_write_failure_retains_prior_atomic_batches_and_finalizes_partial():
    captures = InMemoryCaptureRepository()
    stored_packets = InMemoryPacketRepository(captures)
    packets = FailingPackets(stored_packets)
    service = IngestService(
        captures,
        packets,
        FrozenClock(NOW),
        SequenceIdGenerator((CaptureId("cap_ingest_fixture"),)),
        batch_size=2,
    )

    with pytest.raises(OSError, match="storage unavailable"):
        service.ingest(
            _live_spec(),
            SequencePacketSource(
                (_observation(1), _observation(2), _observation(3), _observation(4))
            ),
        )

    result = captures.get(CaptureId("cap_ingest_fixture"))
    assert result is not None
    assert result.state is CaptureState.PARTIAL
    assert result.packet_count == 2
    assert result.failure_code == "OSError"
    assert packets.batch_sizes == [2, 2, 2]
    assert len(stored_packets.read(ObservationWindow(capture_ids=(result.capture_id,)))) == 2


def test_cooperative_cancellation_flushes_pending_batch_and_returns_cancelled_record():
    service, captures, stored_packets, packets = _service(batch_size=10)
    signal = SequenceCancellationSignal((False, False, True))

    result = service.ingest(
        _live_spec(),
        SequencePacketSource((_observation(1), _observation(2), _observation(3))),
        cancellation=signal,
    )

    assert result.state is CaptureState.CANCELLED
    assert result.packet_count == 2
    assert result.failure_code == "capture_cancelled"
    assert captures.get(result.capture_id) == result
    assert packets.batch_sizes == [2]
    assert len(stored_packets.read(ObservationWindow(capture_ids=(result.capture_id,)))) == 2


def test_keyboard_interrupt_finalizes_cancelled_before_propagating():
    service, captures, _, _ = _service(batch_size=10)

    with pytest.raises(KeyboardInterrupt):
        service.ingest(_live_spec(), FailingSource((_observation(1),), KeyboardInterrupt()))

    result = captures.get(CaptureId("cap_ingest_fixture"))
    assert result is not None
    assert result.state is CaptureState.CANCELLED
    assert result.packet_count == 1
    assert result.failure_code == "capture_cancelled"


def test_ingest_batch_size_must_be_positive():
    captures = InMemoryCaptureRepository()
    packets = InMemoryPacketRepository(captures)
    with pytest.raises(ValueError, match="batch_size must be positive"):
        IngestService(
            captures,
            packets,
            FrozenClock(NOW),
            SequenceIdGenerator((CaptureId("unused"),)),
            batch_size=0,
        )
