"""Portable, checksummed snapshots of versioned JAWS evidence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Mapping

from .captures import CaptureRecord
from .enrichment import EnrichmentRecord, ResearcherAnnotation, normalized_ip
from .enums import ObservationScopeKind, OutlierStatus, ProfileStatus
from .identifiers import CanonicalDigest, CaptureId, EntityId, ObservationScopeId
from .packets import PacketRecord
from .serialization import canonical_digest, primitive
from .time import normalize_utc

EVIDENCE_BUNDLE_FORMAT = "jaws-evidence-bundle"
EVIDENCE_BUNDLE_VERSION = 1
EVIDENCE_SECTIONS = (
    "captures",
    "observation_scopes",
    "packets",
    "entities",
    "enrichments",
    "annotations",
    "profiles",
)


def _optional_text(value: str | None) -> str | None:
    text = value.strip() if value else None
    return text or None


def _sha256(value: str, name: str) -> str:
    text = value.strip()
    if len(text) != 64 or any(character not in "0123456789abcdef" for character in text):
        raise ValueError(f"{name} must be lowercase SHA-256 text")
    return text


@dataclass(frozen=True, slots=True, order=True)
class SchemaMigrationProvenance:
    version: int
    name: str
    checksum: str

    def __post_init__(self) -> None:
        name = self.name.strip()
        if self.version < 1 or not name:
            raise ValueError("schema migration provenance requires a positive version and name")
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "checksum", _sha256(self.checksum, "migration checksum"))


@dataclass(frozen=True, slots=True)
class EvidenceSchemaProvenance:
    version: int
    migrations: tuple[SchemaMigrationProvenance, ...]

    def __post_init__(self) -> None:
        if self.version < 1:
            raise ValueError("evidence schema version must be positive")
        ordered = tuple(sorted(self.migrations))
        versions = tuple(item.version for item in ordered)
        if versions != tuple(range(1, self.version + 1)):
            raise ValueError("evidence schema migrations must be contiguous through its version")
        object.__setattr__(self, "migrations", ordered)


@dataclass(frozen=True, slots=True)
class ArchivedObservationScope:
    scope_id: ObservationScopeId
    kind: ObservationScopeKind | None
    created_at: datetime | None
    capture_ids: tuple[CaptureId, ...] = ()
    perspective: EntityId | None = None
    filters: tuple[str, ...] = ()
    quarantined: bool = False

    def __post_init__(self) -> None:
        capture_ids = tuple(sorted(self.capture_ids))
        if len(capture_ids) != len(set(capture_ids)):
            raise ValueError("archived scope capture IDs must be unique")
        if self.kind is ObservationScopeKind.CAPTURE and len(capture_ids) != 1:
            raise ValueError("archived capture scope requires exactly one capture")
        filters = tuple(value.strip() for value in self.filters)
        if any(not value for value in filters):
            raise ValueError("archived scope filters cannot be empty")
        object.__setattr__(self, "capture_ids", capture_ids)
        object.__setattr__(self, "filters", filters)
        if self.created_at is not None:
            object.__setattr__(self, "created_at", normalize_utc(self.created_at))


@dataclass(frozen=True, slots=True)
class ArchivedEntity:
    entity_id: EntityId
    ip_address: str
    organizations: tuple[str, ...] = ()
    hostname: str | None = None
    location: str | None = None
    coordinates: str | None = None

    def __post_init__(self) -> None:
        address = normalized_ip(self.ip_address)
        if self.entity_id != EntityId(f"ip:{address}"):
            raise ValueError("archived entity ID must match its normalized IP address")
        organizations = tuple(sorted({value.strip() for value in self.organizations}))
        if any(not value for value in organizations):
            raise ValueError("archived entity organizations cannot be empty")
        object.__setattr__(self, "ip_address", address)
        object.__setattr__(self, "organizations", organizations)
        for field_name in ("hostname", "location", "coordinates"):
            object.__setattr__(self, field_name, _optional_text(getattr(self, field_name)))


@dataclass(frozen=True, slots=True)
class ArchivedProfile:
    """Stored profile fields without inventing identity for legacy records."""

    scope_id: ObservationScopeId
    entity_id: EntityId
    profile_key: str | None
    legacy_scope: str | None
    representation_id: str | None
    representation_version: str | None
    model_id: str | None
    model_revision: str | None
    computed_at: datetime | None
    address_classification: str | None
    organization: str | None = None
    hostname: str | None = None
    location: str | None = None
    bytes_out: int = 0
    packets_out: int = 0
    out_peers: int = 0
    out_ports: tuple[int, ...] = ()
    bytes_in: int = 0
    packets_in: int = 0
    in_peers: int = 0
    in_ports: tuple[int, ...] = ()
    protocols: tuple[str, ...] = ()
    interval_mean: float | None = None
    interval_cv: float | None = None
    embedding: tuple[float, ...] = ()
    outlier: OutlierStatus = OutlierStatus.NOT_SCORED
    status: ProfileStatus = ProfileStatus.CURRENT

    def __post_init__(self) -> None:
        for field_name in (
            "profile_key",
            "legacy_scope",
            "representation_id",
            "representation_version",
            "model_id",
            "model_revision",
            "address_classification",
            "organization",
            "hostname",
            "location",
        ):
            object.__setattr__(self, field_name, _optional_text(getattr(self, field_name)))
        if (self.model_id is None) != (self.model_revision is None):
            raise ValueError("archived profile model ID and revision must be provided together")
        for field_name in (
            "bytes_out",
            "packets_out",
            "out_peers",
            "bytes_in",
            "packets_in",
            "in_peers",
        ):
            if getattr(self, field_name) < 0:
                raise ValueError(f"archived profile {field_name} cannot be negative")
        if self.computed_at is not None:
            object.__setattr__(self, "computed_at", normalize_utc(self.computed_at))
        object.__setattr__(self, "out_ports", tuple(sorted(set(self.out_ports))))
        object.__setattr__(self, "in_ports", tuple(sorted(set(self.in_ports))))
        object.__setattr__(self, "protocols", tuple(sorted(set(self.protocols))))
        object.__setattr__(self, "embedding", tuple(self.embedding))


@dataclass(frozen=True, slots=True)
class EvidenceSnapshot:
    schema: EvidenceSchemaProvenance
    captures: tuple[CaptureRecord, ...] = ()
    observation_scopes: tuple[ArchivedObservationScope, ...] = ()
    packets: tuple[PacketRecord, ...] = ()
    entities: tuple[ArchivedEntity, ...] = ()
    enrichments: tuple[EnrichmentRecord, ...] = ()
    annotations: tuple[ResearcherAnnotation, ...] = ()
    profiles: tuple[ArchivedProfile, ...] = ()

    def __post_init__(self) -> None:
        captures = tuple(sorted(self.captures, key=lambda item: item.capture_id.value))
        scopes = tuple(sorted(self.observation_scopes, key=lambda item: item.scope_id.value))
        packets = tuple(
            sorted(
                self.packets,
                key=lambda item: (
                    item.capture_id.value,
                    item.observed_at,
                    item.source_ip,
                    item.destination_ip,
                    item.source_port or 0,
                    item.destination_port or 0,
                ),
            )
        )
        entities = tuple(sorted(self.entities, key=lambda item: item.entity_id.value))
        enrichments = tuple(sorted(self.enrichments, key=lambda item: item.entity_id.value))
        annotations = tuple(
            sorted(
                self.annotations,
                key=lambda item: (item.entity_id.value, item.key, item.recorded_at),
            )
        )
        profiles = tuple(
            sorted(
                self.profiles,
                key=lambda item: (
                    item.scope_id.value,
                    item.entity_id.value,
                    item.profile_key or "",
                ),
            )
        )
        for name, identities in (
            ("capture", tuple(item.capture_id for item in captures)),
            ("scope", tuple(item.scope_id for item in scopes)),
            ("entity", tuple(item.entity_id for item in entities)),
            ("enrichment", tuple(item.entity_id for item in enrichments)),
            ("annotation", tuple((item.entity_id, item.key) for item in annotations)),
        ):
            if len(identities) != len(set(identities)):
                raise ValueError(f"evidence snapshot {name} identities must be unique")
        capture_ids = {item.capture_id for item in captures}
        if any(
            capture_id not in capture_ids for scope in scopes for capture_id in scope.capture_ids
        ):
            raise ValueError("every archived scope member must reference an archived capture")
        entity_ids = {item.entity_id for item in entities}
        if any(item.entity_id not in entity_ids for item in enrichments):
            raise ValueError("every archived enrichment must reference an archived entity")
        if any(item.entity_id not in entity_ids for item in annotations):
            raise ValueError("every archived annotation must reference an archived entity")
        if any(item.entity_id not in entity_ids for item in profiles):
            raise ValueError("every archived profile must reference an archived entity")
        scope_ids = {item.scope_id for item in scopes}
        if any(item.scope_id not in scope_ids for item in profiles):
            raise ValueError("every archived profile must reference an archived scope")
        object.__setattr__(self, "captures", captures)
        object.__setattr__(self, "observation_scopes", scopes)
        object.__setattr__(self, "packets", packets)
        object.__setattr__(self, "entities", entities)
        object.__setattr__(self, "enrichments", enrichments)
        object.__setattr__(self, "annotations", annotations)
        object.__setattr__(self, "profiles", profiles)

    @property
    def sections(self) -> Mapping[str, tuple[object, ...]]:
        return MappingProxyType(
            {
                "captures": self.captures,
                "observation_scopes": self.observation_scopes,
                "packets": self.packets,
                "entities": self.entities,
                "enrichments": self.enrichments,
                "annotations": self.annotations,
                "profiles": self.profiles,
            }
        )

    @property
    def record_counts(self) -> Mapping[str, int]:
        return MappingProxyType({name: len(records) for name, records in self.sections.items()})

    @property
    def section_checksums(self) -> Mapping[str, CanonicalDigest]:
        return MappingProxyType(
            {name: canonical_digest(records) for name, records in self.sections.items()}
        )

    @property
    def content_checksum(self) -> CanonicalDigest:
        return canonical_digest(
            {
                "schema": self.schema,
                "record_counts": self.record_counts,
                "section_checksums": self.section_checksums,
            }
        )


@dataclass(frozen=True, slots=True)
class EvidenceBundleManifest:
    exported_at: datetime
    schema: EvidenceSchemaProvenance
    record_counts: Mapping[str, int]
    section_checksums: Mapping[str, CanonicalDigest]
    content_checksum: CanonicalDigest
    format_id: str = EVIDENCE_BUNDLE_FORMAT
    format_version: int = EVIDENCE_BUNDLE_VERSION

    def __post_init__(self) -> None:
        if self.format_id != EVIDENCE_BUNDLE_FORMAT or self.format_version != 1:
            raise ValueError("unsupported evidence bundle format")
        object.__setattr__(self, "exported_at", normalize_utc(self.exported_at))
        counts = dict(self.record_counts)
        checksums = dict(self.section_checksums)
        if set(counts) != set(EVIDENCE_SECTIONS):
            raise ValueError("evidence manifest must count every section")
        if set(checksums) != set(EVIDENCE_SECTIONS):
            raise ValueError("evidence manifest must checksum every section")
        if any(value < 0 for value in counts.values()):
            raise ValueError("evidence manifest counts cannot be negative")
        normalized_checksums = {
            name: CanonicalDigest(_sha256(str(value), f"{name} checksum"))
            for name, value in checksums.items()
        }
        object.__setattr__(self, "record_counts", MappingProxyType(dict(sorted(counts.items()))))
        object.__setattr__(
            self,
            "section_checksums",
            MappingProxyType(dict(sorted(normalized_checksums.items()))),
        )
        object.__setattr__(
            self,
            "content_checksum",
            CanonicalDigest(_sha256(str(self.content_checksum), "content checksum")),
        )


@dataclass(frozen=True, slots=True)
class EvidenceBundle:
    manifest: EvidenceBundleManifest
    evidence: EvidenceSnapshot

    def __post_init__(self) -> None:
        if self.manifest.schema != self.evidence.schema:
            raise ValueError("evidence bundle schema provenance does not match its manifest")
        if dict(self.manifest.record_counts) != dict(self.evidence.record_counts):
            raise ValueError("evidence bundle record counts do not match its contents")
        if dict(self.manifest.section_checksums) == dict(self.evidence.section_checksums):
            if self.manifest.content_checksum != self.evidence.content_checksum:
                raise ValueError("evidence bundle content checksum mismatch")
            return

        # Format version 1 predates optional CaptureRecord.source_metadata. Verify
        # those bundles against their original shape, but only when no metadata
        # would be omitted from integrity protection.
        if any(capture.source_metadata is not None for capture in self.evidence.captures):
            raise ValueError("evidence bundle section checksum mismatch")
        legacy_captures = []
        for capture in self.evidence.captures:
            record = primitive(capture)
            assert isinstance(record, dict)
            record.pop("source_metadata")
            legacy_captures.append(record)
        legacy_checksums = dict(self.evidence.section_checksums)
        legacy_checksums["captures"] = canonical_digest(legacy_captures)
        if dict(self.manifest.section_checksums) != legacy_checksums:
            raise ValueError("evidence bundle section checksum mismatch")
        legacy_content = canonical_digest(
            {
                "schema": self.evidence.schema,
                "record_counts": self.evidence.record_counts,
                "section_checksums": legacy_checksums,
            }
        )
        if self.manifest.content_checksum != legacy_content:
            raise ValueError("evidence bundle content checksum mismatch")

    @classmethod
    def build(cls, evidence: EvidenceSnapshot, *, exported_at: datetime) -> EvidenceBundle:
        return cls(
            manifest=EvidenceBundleManifest(
                exported_at=exported_at,
                schema=evidence.schema,
                record_counts=evidence.record_counts,
                section_checksums=evidence.section_checksums,
                content_checksum=evidence.content_checksum,
            ),
            evidence=evidence,
        )


@dataclass(frozen=True, slots=True)
class EvidenceImportPlan:
    schema: EvidenceSchemaProvenance
    record_counts: Mapping[str, int]
    content_checksum: CanonicalDigest
    target_empty: bool

    def __post_init__(self) -> None:
        object.__setattr__(self, "record_counts", MappingProxyType(dict(self.record_counts)))


@dataclass(frozen=True, slots=True)
class EvidenceImportResult:
    plan: EvidenceImportPlan
    applied: bool
