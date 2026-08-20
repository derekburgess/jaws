"""Provider-neutral enrichment and researcher annotation records."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from ipaddress import IPv4Address, ip_address
from typing import Literal

from .enums import EnrichmentStatus
from .identifiers import EntityId
from .time import normalize_utc

AddressClassification = Literal[
    "multicast",
    "broadcast",
    "unspecified",
    "loopback",
    "link-local",
    "private",
    "public",
    "reserved",
    "unknown",
]
NON_CONVERSATIONAL_CLASSIFICATIONS = frozenset({"multicast", "broadcast", "unspecified"})


def normalized_ip(value: str) -> str:
    text = value.strip()
    if not text:
        raise ValueError("IP address cannot be empty")
    try:
        return str(ip_address(text))
    except ValueError as error:
        raise ValueError("IP address must be valid IPv4 or IPv6 text") from error


def classify_ip_address(value: str) -> AddressClassification:
    """Classify address scope deterministically without consulting a provider."""

    try:
        address = ip_address(value.strip())
    except ValueError:
        return "unknown"
    if address.is_multicast:
        return "multicast"
    if address == IPv4Address("255.255.255.255"):
        return "broadcast"
    if address.is_unspecified:
        return "unspecified"
    if address.is_loopback:
        return "loopback"
    if address.is_link_local:
        return "link-local"
    if address.is_private:
        return "private"
    if address.is_global:
        return "public"
    return "reserved"


def _optional_text(value: str | None) -> str | None:
    text = value.strip() if value else None
    return text or None


@dataclass(frozen=True, slots=True)
class EnrichmentObservation:
    """Provider-neutral response before entity ownership and acquisition time are bound."""

    status: EnrichmentStatus
    provider_id: str
    provider_revision: str
    organization: str | None = None
    asn: str | None = None
    hostname: str | None = None
    location: str | None = None
    coordinates: str | None = None
    confidence: float | None = None
    failure_code: str | None = None

    def __post_init__(self) -> None:
        provider_id = self.provider_id.strip()
        provider_revision = self.provider_revision.strip()
        if not provider_id or not provider_revision:
            raise ValueError("enrichment provider ID and revision cannot be empty")
        if self.confidence is not None and not 0.0 <= self.confidence <= 1.0:
            raise ValueError("enrichment confidence must be between zero and one")
        normalized_metadata = tuple(
            _optional_text(value)
            for value in (
                self.organization,
                self.asn,
                self.hostname,
                self.location,
                self.coordinates,
            )
        )
        if self.status is not EnrichmentStatus.SUCCEEDED and (
            any(normalized_metadata) or self.confidence is not None
        ):
            raise ValueError("unsuccessful enrichment cannot carry resolved metadata")
        if self.status is EnrichmentStatus.SUCCEEDED and not any(normalized_metadata):
            raise ValueError("successful enrichment requires resolved metadata")
        object.__setattr__(self, "provider_id", provider_id)
        object.__setattr__(self, "provider_revision", provider_revision)
        for field_name, value in zip(
            ("organization", "asn", "hostname", "location", "coordinates"),
            normalized_metadata,
            strict=True,
        ):
            object.__setattr__(self, field_name, value)
        object.__setattr__(self, "failure_code", _optional_text(self.failure_code))

    def for_address(self, value: str, acquired_at: datetime) -> EnrichmentRecord:
        """Bind the provider response to one normalized IP entity and acquisition time."""

        address = normalized_ip(value)
        return EnrichmentRecord(
            entity_id=EntityId(f"ip:{address}"),
            ip_address=address,
            status=self.status,
            acquired_at=acquired_at,
            provider_id=self.provider_id,
            provider_revision=self.provider_revision,
            organization=self.organization,
            asn=self.asn,
            hostname=self.hostname,
            location=self.location,
            coordinates=self.coordinates,
            confidence=self.confidence,
            failure_code=self.failure_code,
        )


@dataclass(frozen=True, slots=True)
class EnrichmentRecord:
    """One provider observation about an IP entity at an explicit acquisition time."""

    entity_id: EntityId
    ip_address: str
    status: EnrichmentStatus
    acquired_at: datetime
    provider_id: str
    provider_revision: str
    organization: str | None = None
    asn: str | None = None
    hostname: str | None = None
    location: str | None = None
    coordinates: str | None = None
    confidence: float | None = None
    failure_code: str | None = None

    def __post_init__(self) -> None:
        address = normalized_ip(self.ip_address)
        if self.entity_id != EntityId(f"ip:{address}"):
            raise ValueError("enrichment entity_id must match its normalized IP address")
        provider_id = self.provider_id.strip()
        provider_revision = self.provider_revision.strip()
        if not provider_id or not provider_revision:
            raise ValueError("enrichment provider ID and revision cannot be empty")
        if self.confidence is not None and not 0.0 <= self.confidence <= 1.0:
            raise ValueError("enrichment confidence must be between zero and one")
        normalized_metadata = tuple(
            _optional_text(value)
            for value in (
                self.organization,
                self.asn,
                self.hostname,
                self.location,
                self.coordinates,
            )
        )
        if self.status is not EnrichmentStatus.SUCCEEDED and any(normalized_metadata):
            raise ValueError("unsuccessful enrichment cannot carry resolved metadata")
        if self.status is EnrichmentStatus.SUCCEEDED and not any(normalized_metadata):
            raise ValueError("successful enrichment requires resolved metadata")
        object.__setattr__(self, "ip_address", address)
        object.__setattr__(self, "acquired_at", normalize_utc(self.acquired_at))
        object.__setattr__(self, "provider_id", provider_id)
        object.__setattr__(self, "provider_revision", provider_revision)
        for field_name, value in zip(
            ("organization", "asn", "hostname", "location", "coordinates"),
            normalized_metadata,
            strict=True,
        ):
            object.__setattr__(self, field_name, value)
        object.__setattr__(self, "failure_code", _optional_text(self.failure_code))


@dataclass(frozen=True, slots=True)
class ResearcherAnnotation:
    """Researcher-authored metadata kept separate from provider enrichment."""

    entity_id: EntityId
    key: str
    value: str
    author: str
    recorded_at: datetime
    ground_truth: bool = False

    def __post_init__(self) -> None:
        for field_name in ("key", "value", "author"):
            value = getattr(self, field_name).strip()
            if not value:
                raise ValueError(f"annotation {field_name} cannot be empty")
            object.__setattr__(self, field_name, value)
        object.__setattr__(self, "recorded_at", normalize_utc(self.recorded_at))


@dataclass(frozen=True, slots=True)
class EntityMetadata:
    """Compatibility projection of display metadata attached to one IP entity.

    This view is deliberately not an enrichment observation: legacy ownership and local
    host markers may have no provider provenance. Provider claims remain authoritative in
    :class:`EnrichmentRecord`.
    """

    entity_id: EntityId
    ip_address: str
    organization: str | None = None
    hostname: str | None = None
    location: str | None = None
    coordinates: str | None = None

    def __post_init__(self) -> None:
        address = normalized_ip(self.ip_address)
        if self.entity_id != EntityId(f"ip:{address}"):
            raise ValueError("metadata entity_id must match its normalized IP address")
        object.__setattr__(self, "ip_address", address)
        for field_name in ("organization", "hostname", "location", "coordinates"):
            object.__setattr__(self, field_name, _optional_text(getattr(self, field_name)))
