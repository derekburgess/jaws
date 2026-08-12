"""Typed packet evidence retained by capture-scoped repositories."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from ipaddress import ip_address

from .identifiers import CaptureId
from .time import normalize_utc


def _ip_text(value: str) -> str:
    text = value.strip()
    if not text:
        raise ValueError("packet IP address cannot be empty")
    try:
        return str(ip_address(text))
    except ValueError as error:
        raise ValueError("packet IP address must be valid IPv4 or IPv6 text") from error


def _port(value: int | None) -> int | None:
    if value is None:
        return None
    if not 1 <= value <= 65535:
        raise ValueError("packet port must be between 1 and 65535 when present")
    return value


@dataclass(frozen=True, slots=True)
class PacketRecord:
    """One immutable packet observation with explicit capture ownership."""

    capture_id: CaptureId
    observed_at: datetime
    protocol: str
    size_bytes: int
    source_ip: str
    destination_ip: str
    source_port: int | None = None
    destination_port: int | None = None
    payload: str | None = None

    def __post_init__(self) -> None:
        protocol = self.protocol.strip()
        if not protocol:
            raise ValueError("packet protocol cannot be empty")
        if self.size_bytes < 0:
            raise ValueError("packet size cannot be negative")
        object.__setattr__(self, "observed_at", normalize_utc(self.observed_at))
        object.__setattr__(self, "protocol", protocol)
        object.__setattr__(self, "source_ip", _ip_text(self.source_ip))
        object.__setattr__(self, "destination_ip", _ip_text(self.destination_ip))
        object.__setattr__(self, "source_port", _port(self.source_port))
        object.__setattr__(self, "destination_port", _port(self.destination_port))
        if self.payload is not None and not isinstance(self.payload, str):
            raise ValueError("packet payload must be text or null")
