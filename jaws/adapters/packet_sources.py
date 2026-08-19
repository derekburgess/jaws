"""PyShark-facing packet parsing and bounded live/PCAP source adapters."""

from __future__ import annotations

import hashlib
import subprocess
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from queue import Empty, Full, Queue
from threading import Event, Thread
from typing import Any, Protocol

from jaws.domain import PacketObservation, normalize_utc

PacketObserver = Callable[[PacketObservation, str], None]


class _CompletedProcess(Protocol):
    stdout: str


class _Runner(Protocol):
    def __call__(
        self,
        args: Sequence[str],
        *,
        capture_output: bool,
        text: bool,
        check: bool,
        timeout: int,
    ) -> _CompletedProcess: ...


def file_sha256(path: str | Path) -> str:
    """Hash a file through bounded reads without treating its path as identity."""

    digest = hashlib.sha256()
    with Path(path).open("rb") as capture_file:
        for chunk in iter(lambda: capture_file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def capture_tool_versions(
    *,
    package_version: Callable[[str], str] = version,
    runner: _Runner = subprocess.run,
) -> dict[str, str]:
    """Return non-secret package/runtime provenance, retaining explicit unknowns."""

    versions: dict[str, str] = {}
    for distribution in ("JAWS", "pyshark"):
        try:
            versions[distribution.lower()] = package_version(distribution)
        except PackageNotFoundError:
            versions[distribution.lower()] = "unknown"
    try:
        completed = runner(
            ("tshark", "--version"),
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        first_line = completed.stdout.splitlines()[0].strip() if completed.stdout else ""
        versions["tshark"] = first_line or "unknown"
    except (OSError, subprocess.SubprocessError):
        versions["tshark"] = "unknown"
    return versions


@dataclass(slots=True)
class PacketParseStats:
    seen: int = 0
    emitted: int = 0
    skipped_non_ip: int = 0
    skipped_malformed: int = 0


def packet_summary(observation: PacketObservation) -> str:
    source_port = observation.source_port or 0
    destination_port = observation.destination_port or 0
    return (
        f"{observation.source_ip}:{source_port} ➜ {observation.protocol}"
        f"({observation.size_bytes}) ➜ "
        f"{observation.destination_ip}:{destination_port}"
    )


def _layers(packet: Any, name: str) -> tuple[Any, ...]:
    ordered = tuple(
        layer
        for layer in (getattr(packet, "layers", None) or ())
        if str(getattr(layer, "layer_name", "")).lower() == name
    )
    if ordered:
        return ordered
    getter = getattr(packet, "get_multiple_layers", None)
    if callable(getter):
        try:
            multiple = tuple(getter(name) or ())
        except (AttributeError, KeyError, TypeError, ValueError):
            multiple = ()
        if multiple:
            return multiple
    layer = getattr(packet, name, None)
    return (layer,) if layer is not None else ()


def _ip_pair(packet: Any) -> tuple[str, str] | None:
    packet_layers = tuple(getattr(packet, "layers", None) or ())
    candidates = tuple(
        layer
        for layer in packet_layers
        if str(getattr(layer, "layer_name", "")).lower() in {"ip", "ipv6"}
    )
    if not candidates:
        candidates = _layers(packet, "ip") + _layers(packet, "ipv6")
    for layer in candidates:
        source = str(getattr(layer, "src", "")).strip()
        destination = str(getattr(layer, "dst", "")).strip()
        if source and destination:
            return source, destination
    return None


def _timestamp(packet: Any) -> datetime:
    epoch = getattr(packet, "sniff_timestamp", None)
    if epoch is not None:
        return datetime.fromtimestamp(float(str(epoch)), UTC)
    observed_at = getattr(packet, "sniff_time", None)
    if not isinstance(observed_at, datetime):
        raise ValueError("packet has no source timestamp")
    return normalize_utc(observed_at)


def _port(value: object) -> int | None:
    if value is None:
        return None
    try:
        parsed = int(str(value))
    except ValueError:
        return None
    return parsed if 1 <= parsed <= 65535 else None


def _transport(packet: Any) -> tuple[int | None, int | None, str | None]:
    for name in ("tcp", "udp"):
        layers = _layers(packet, name)
        if not layers:
            continue
        layer = layers[0]
        payload = getattr(layer, "payload", None)
        payload_text = str(payload) if payload is not None else None
        return (
            _port(getattr(layer, "srcport", None)),
            _port(getattr(layer, "dstport", None)),
            payload_text,
        )
    return None, None, None


def _protocol(packet: Any) -> str:
    highest_value = getattr(packet, "highest_layer", None)
    highest = str(highest_value).strip() if highest_value is not None else ""
    if highest:
        return highest
    for name, label in (
        ("tcp", "TCP"),
        ("udp", "UDP"),
        ("icmp", "ICMP"),
        ("icmpv6", "ICMPV6"),
        ("ip", "IP"),
        ("ipv6", "IPV6"),
    ):
        if _layers(packet, name):
            return label
    raise ValueError("packet has no decoded protocol")


@dataclass(slots=True)
class PySharkPacketParser:
    """Convert supported decoded IP frames into provider-neutral observations."""

    stats: PacketParseStats = field(default_factory=PacketParseStats)

    def parse(self, packet: Any) -> PacketObservation | None:
        self.stats.seen += 1
        addresses = _ip_pair(packet)
        if addresses is None:
            self.stats.skipped_non_ip += 1
            return None
        try:
            source_port, destination_port, payload = _transport(packet)
            observation = PacketObservation(
                observed_at=_timestamp(packet),
                protocol=_protocol(packet),
                size_bytes=len(packet),
                source_ip=addresses[0],
                destination_ip=addresses[1],
                source_port=source_port,
                destination_port=destination_port,
                payload=payload,
            )
        except (AttributeError, OverflowError, TypeError, ValueError):
            self.stats.skipped_malformed += 1
            return None
        self.stats.emitted += 1
        return observation


def _close(capture: Any) -> None:
    close = getattr(capture, "close", None)
    if callable(close):
        try:
            close()
        except Exception:
            pass


@dataclass(slots=True)
class PcapPacketSource:
    """Stream one PCAP through PyShark without retaining decoded packets in memory."""

    pyshark: Any
    path: str
    display_filter: str | None = None
    parser: PySharkPacketParser = field(default_factory=PySharkPacketParser)
    observer: PacketObserver | None = None

    def packets(self) -> Iterator[PacketObservation]:
        factory = getattr(self.pyshark, "FileCapture")
        parameters: dict[str, object] = {"keep_packets": False}
        if self.display_filter:
            parameters["display_filter"] = self.display_filter
        capture = factory(self.path, **parameters)
        try:
            for packet in capture:
                observation = self.parser.parse(packet)
                if observation is None:
                    continue
                if self.observer is not None:
                    self.observer(observation, packet_summary(observation))
                yield observation
        finally:
            _close(capture)


@dataclass(frozen=True, slots=True)
class _WorkerFailure:
    error: BaseException


@dataclass(frozen=True, slots=True)
class _WorkerComplete:
    pass


_COMPLETE = _WorkerComplete()
_Event = PacketObservation | _WorkerFailure | _WorkerComplete


@dataclass(slots=True)
class LivePacketSource:
    """Bridge PyShark's callback API to a bounded packet-observation iterator."""

    pyshark: Any
    interface: str
    duration_seconds: int
    capture_filter: str | None = None
    display_filter: str | None = None
    parser: PySharkPacketParser = field(default_factory=PySharkPacketParser)
    observer: PacketObserver | None = None
    queue_size: int = 1

    def __post_init__(self) -> None:
        if self.duration_seconds < 1:
            raise ValueError("live capture duration must be positive")
        if self.queue_size < 1:
            raise ValueError("live packet queue_size must be positive")

    def packets(self) -> Iterator[PacketObservation]:
        factory = getattr(self.pyshark, "LiveCapture")
        parameters: dict[str, object] = {"interface": self.interface}
        if self.capture_filter:
            parameters["bpf_filter"] = self.capture_filter
        if self.display_filter:
            parameters["display_filter"] = self.display_filter
        capture = factory(**parameters)
        events: Queue[_Event] = Queue(maxsize=self.queue_size)
        stopped = Event()

        def put(event: _Event) -> None:
            while not stopped.is_set():
                try:
                    events.put(event, timeout=0.1)
                    return
                except Full:
                    continue

        def observe(packet: Any) -> None:
            observation = self.parser.parse(packet)
            if observation is None:
                return
            put(observation)

        def capture_packets() -> None:
            try:
                capture.apply_on_packets(observe, timeout=self.duration_seconds)
            except TimeoutError:
                pass
            except BaseException as error:
                put(_WorkerFailure(error))
            finally:
                put(_COMPLETE)

        worker = Thread(target=capture_packets, name="jaws-live-capture", daemon=True)
        worker.start()
        try:
            while True:
                try:
                    event = events.get(timeout=0.1)
                except Empty:
                    if not worker.is_alive():
                        break
                    continue
                if isinstance(event, _WorkerComplete):
                    break
                if isinstance(event, _WorkerFailure):
                    raise event.error
                if self.observer is not None:
                    self.observer(event, packet_summary(event))
                yield event
        finally:
            stopped.set()
            _close(capture)
            worker.join(timeout=1)
