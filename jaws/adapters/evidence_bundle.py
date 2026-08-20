"""Atomic JSON encoding and strict decoding for portable evidence bundles."""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from jaws.domain import (
    ArchivedEntity,
    ArchivedObservationScope,
    ArchivedProfile,
    CanonicalDigest,
    CaptureId,
    CaptureRecord,
    CaptureSourceKind,
    CaptureSourceMetadata,
    CaptureState,
    EnrichmentRecord,
    EnrichmentStatus,
    EntityId,
    EvidenceBundle,
    EvidenceBundleManifest,
    EvidenceSchemaProvenance,
    EvidenceSnapshot,
    ObservationScopeId,
    ObservationScopeKind,
    OutlierStatus,
    PacketRecord,
    ProfileStatus,
    ResearcherAnnotation,
    SchemaMigrationProvenance,
    primitive,
)


class EvidenceBundleError(ValueError):
    """A portable bundle is malformed, unsupported, or checksum-invalid."""


def _mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise EvidenceBundleError(f"{name} must be a JSON object")
    return value


def _sequence(value: object, name: str) -> Sequence[Any]:
    if not isinstance(value, list):
        raise EvidenceBundleError(f"{name} must be a JSON array")
    return value


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise EvidenceBundleError(f"{name} must be non-empty text")
    return value


def _optional_text(value: object, name: str) -> str | None:
    return None if value is None else _text(value, name)


def _payload(value: object) -> str | None:
    if value is None or isinstance(value, str):
        return value
    raise EvidenceBundleError("packet payload must be text or null")


def _integer(value: object, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise EvidenceBundleError(f"{name} must be an integer")
    return value


def _number(value: object, name: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise EvidenceBundleError(f"{name} must be numeric")
    return float(value)


def _optional_number(value: object, name: str) -> float | None:
    return None if value is None else _number(value, name)


def _boolean(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise EvidenceBundleError(f"{name} must be boolean")
    return value


def _datetime(value: object, name: str) -> datetime:
    text = _text(value, name)
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as error:
        raise EvidenceBundleError(f"{name} must be an ISO-8601 timestamp") from error


def _optional_datetime(value: object, name: str) -> datetime | None:
    return None if value is None else _datetime(value, name)


def _text_tuple(value: object, name: str) -> tuple[str, ...]:
    return tuple(_text(item, name) for item in _sequence(value, name))


def _integer_tuple(value: object, name: str) -> tuple[int, ...]:
    return tuple(_integer(item, name) for item in _sequence(value, name))


def _number_tuple(value: object, name: str) -> tuple[float, ...]:
    return tuple(_number(item, name) for item in _sequence(value, name))


def _migration(value: object) -> SchemaMigrationProvenance:
    record = _mapping(value, "schema migration")
    return SchemaMigrationProvenance(
        version=_integer(record.get("version"), "schema migration version"),
        name=_text(record.get("name"), "schema migration name"),
        checksum=_text(record.get("checksum"), "schema migration checksum"),
    )


def _schema(value: object) -> EvidenceSchemaProvenance:
    record = _mapping(value, "schema provenance")
    return EvidenceSchemaProvenance(
        version=_integer(record.get("version"), "schema version"),
        migrations=tuple(
            _migration(item) for item in _sequence(record.get("migrations"), "schema migrations")
        ),
    )


def _capture(value: object) -> CaptureRecord:
    record = _mapping(value, "capture")
    tool_versions = _mapping(record.get("tool_versions"), "capture tool_versions")
    digest = record.get("content_digest")
    perspective = record.get("perspective")
    source_metadata_value = record.get("source_metadata")
    source_metadata = None
    if source_metadata_value is not None:
        metadata = _mapping(source_metadata_value, "capture source metadata")
        source_metadata = CaptureSourceMetadata(
            file_name=_text(metadata.get("file_name"), "capture source file name"),
            size_bytes=_integer(metadata.get("size_bytes"), "capture source size bytes"),
            source_locator=_optional_text(metadata.get("source_locator"), "capture source locator"),
            locator_portable=_boolean(
                metadata.get("locator_portable"), "capture source locator portability"
            ),
        )
    return CaptureRecord(
        capture_id=CaptureId(_text(record.get("capture_id"), "capture ID")),
        source_kind=CaptureSourceKind(_text(record.get("source_kind"), "capture source kind")),
        source_name=_text(record.get("source_name"), "capture source name"),
        state=CaptureState(_text(record.get("state"), "capture state")),
        registered_at=_datetime(record.get("registered_at"), "capture registered_at"),
        legacy_capture_id=_optional_text(record.get("legacy_capture_id"), "legacy capture ID"),
        started_at=_optional_datetime(record.get("started_at"), "capture started_at"),
        ended_at=_optional_datetime(record.get("ended_at"), "capture ended_at"),
        packet_count=_integer(record.get("packet_count"), "capture packet count"),
        content_digest=(
            CanonicalDigest(_text(digest, "capture content digest")) if digest is not None else None
        ),
        source_metadata=source_metadata,
        perspective=(
            EntityId(_text(perspective, "capture perspective")) if perspective is not None else None
        ),
        capture_filter=_optional_text(record.get("capture_filter"), "capture filter"),
        tool_versions={
            _text(key, "tool name"): _text(item, "tool version")
            for key, item in tool_versions.items()
        },
        failure_code=_optional_text(record.get("failure_code"), "capture failure code"),
    )


def _scope(value: object) -> ArchivedObservationScope:
    record = _mapping(value, "observation scope")
    kind = record.get("kind")
    perspective = record.get("perspective")
    return ArchivedObservationScope(
        scope_id=ObservationScopeId(_text(record.get("scope_id"), "scope ID")),
        kind=ObservationScopeKind(_text(kind, "scope kind")) if kind is not None else None,
        created_at=_optional_datetime(record.get("created_at"), "scope created_at"),
        capture_ids=tuple(
            CaptureId(_text(item, "scope capture ID"))
            for item in _sequence(record.get("capture_ids"), "scope capture IDs")
        ),
        perspective=EntityId(_text(perspective, "scope perspective")) if perspective else None,
        filters=_text_tuple(record.get("filters"), "scope filters"),
        quarantined=_boolean(record.get("quarantined"), "scope quarantined"),
    )


def _packet(value: object) -> PacketRecord:
    record = _mapping(value, "packet")
    source_port = record.get("source_port")
    destination_port = record.get("destination_port")
    return PacketRecord(
        capture_id=CaptureId(_text(record.get("capture_id"), "packet capture ID")),
        observed_at=_datetime(record.get("observed_at"), "packet observed_at"),
        protocol=_text(record.get("protocol"), "packet protocol"),
        size_bytes=_integer(record.get("size_bytes"), "packet size"),
        source_ip=_text(record.get("source_ip"), "packet source IP"),
        destination_ip=_text(record.get("destination_ip"), "packet destination IP"),
        source_port=_integer(source_port, "packet source port")
        if source_port is not None
        else None,
        destination_port=(
            _integer(destination_port, "packet destination port")
            if destination_port is not None
            else None
        ),
        payload=_payload(record.get("payload")),
    )


def _entity(value: object) -> ArchivedEntity:
    record = _mapping(value, "entity")
    return ArchivedEntity(
        entity_id=EntityId(_text(record.get("entity_id"), "entity ID")),
        ip_address=_text(record.get("ip_address"), "entity IP address"),
        organizations=_text_tuple(record.get("organizations"), "entity organizations"),
        hostname=_optional_text(record.get("hostname"), "entity hostname"),
        location=_optional_text(record.get("location"), "entity location"),
        coordinates=_optional_text(record.get("coordinates"), "entity coordinates"),
    )


def _enrichment(value: object) -> EnrichmentRecord:
    record = _mapping(value, "enrichment")
    return EnrichmentRecord(
        entity_id=EntityId(_text(record.get("entity_id"), "enrichment entity ID")),
        ip_address=_text(record.get("ip_address"), "enrichment IP address"),
        status=EnrichmentStatus(_text(record.get("status"), "enrichment status")),
        acquired_at=_datetime(record.get("acquired_at"), "enrichment acquired_at"),
        provider_id=_text(record.get("provider_id"), "enrichment provider ID"),
        provider_revision=_text(record.get("provider_revision"), "enrichment provider revision"),
        organization=_optional_text(record.get("organization"), "enrichment organization"),
        asn=_optional_text(record.get("asn"), "enrichment ASN"),
        hostname=_optional_text(record.get("hostname"), "enrichment hostname"),
        location=_optional_text(record.get("location"), "enrichment location"),
        coordinates=_optional_text(record.get("coordinates"), "enrichment coordinates"),
        confidence=_optional_number(record.get("confidence"), "enrichment confidence"),
        failure_code=_optional_text(record.get("failure_code"), "enrichment failure code"),
    )


def _annotation(value: object) -> ResearcherAnnotation:
    record = _mapping(value, "annotation")
    return ResearcherAnnotation(
        entity_id=EntityId(_text(record.get("entity_id"), "annotation entity ID")),
        key=_text(record.get("key"), "annotation key"),
        value=_text(record.get("value"), "annotation value"),
        author=_text(record.get("author"), "annotation author"),
        recorded_at=_datetime(record.get("recorded_at"), "annotation recorded_at"),
        ground_truth=_boolean(record.get("ground_truth"), "annotation ground_truth"),
    )


def _profile(value: object) -> ArchivedProfile:
    record = _mapping(value, "profile")
    return ArchivedProfile(
        scope_id=ObservationScopeId(_text(record.get("scope_id"), "profile scope ID")),
        entity_id=EntityId(_text(record.get("entity_id"), "profile entity ID")),
        profile_key=_optional_text(record.get("profile_key"), "profile key"),
        legacy_scope=_optional_text(record.get("legacy_scope"), "profile legacy scope"),
        representation_id=_optional_text(
            record.get("representation_id"), "profile representation ID"
        ),
        representation_version=_optional_text(
            record.get("representation_version"), "profile representation version"
        ),
        model_id=_optional_text(record.get("model_id"), "profile model ID"),
        model_revision=_optional_text(record.get("model_revision"), "profile model revision"),
        computed_at=_optional_datetime(record.get("computed_at"), "profile computed_at"),
        address_classification=_optional_text(
            record.get("address_classification"), "profile address classification"
        ),
        organization=_optional_text(record.get("organization"), "profile organization"),
        hostname=_optional_text(record.get("hostname"), "profile hostname"),
        location=_optional_text(record.get("location"), "profile location"),
        bytes_out=_integer(record.get("bytes_out"), "profile bytes_out"),
        packets_out=_integer(record.get("packets_out"), "profile packets_out"),
        out_peers=_integer(record.get("out_peers"), "profile out_peers"),
        out_ports=_integer_tuple(record.get("out_ports"), "profile out_ports"),
        bytes_in=_integer(record.get("bytes_in"), "profile bytes_in"),
        packets_in=_integer(record.get("packets_in"), "profile packets_in"),
        in_peers=_integer(record.get("in_peers"), "profile in_peers"),
        in_ports=_integer_tuple(record.get("in_ports"), "profile in_ports"),
        protocols=_text_tuple(record.get("protocols"), "profile protocols"),
        interval_mean=_optional_number(record.get("interval_mean"), "profile interval_mean"),
        interval_cv=_optional_number(record.get("interval_cv"), "profile interval_cv"),
        embedding=_number_tuple(record.get("embedding"), "profile embedding"),
        outlier=OutlierStatus(_text(record.get("outlier"), "profile outlier status")),
        status=ProfileStatus(_text(record.get("status"), "profile status")),
    )


def evidence_bundle_document(bundle: EvidenceBundle) -> dict[str, Any]:
    return {"manifest": primitive(bundle.manifest), "evidence": primitive(bundle.evidence)}


def _parse_evidence_bundle(document: object) -> EvidenceBundle:
    root = _mapping(document, "evidence bundle")
    manifest_record = _mapping(root.get("manifest"), "evidence manifest")
    evidence_record = _mapping(root.get("evidence"), "evidence payload")
    evidence = EvidenceSnapshot(
        schema=_schema(evidence_record.get("schema")),
        captures=tuple(
            _capture(item) for item in _sequence(evidence_record.get("captures"), "captures")
        ),
        observation_scopes=tuple(
            _scope(item)
            for item in _sequence(evidence_record.get("observation_scopes"), "observation scopes")
        ),
        packets=tuple(
            _packet(item) for item in _sequence(evidence_record.get("packets"), "packets")
        ),
        entities=tuple(
            _entity(item) for item in _sequence(evidence_record.get("entities"), "entities")
        ),
        enrichments=tuple(
            _enrichment(item)
            for item in _sequence(evidence_record.get("enrichments"), "enrichments")
        ),
        annotations=tuple(
            _annotation(item)
            for item in _sequence(evidence_record.get("annotations"), "annotations")
        ),
        profiles=tuple(
            _profile(item) for item in _sequence(evidence_record.get("profiles"), "profiles")
        ),
    )
    counts = _mapping(manifest_record.get("record_counts"), "manifest record counts")
    checksums = _mapping(manifest_record.get("section_checksums"), "manifest section checksums")
    manifest = EvidenceBundleManifest(
        exported_at=_datetime(manifest_record.get("exported_at"), "manifest exported_at"),
        schema=_schema(manifest_record.get("schema")),
        record_counts={
            name: _integer(value, f"{name} record count") for name, value in counts.items()
        },
        section_checksums={
            name: CanonicalDigest(_text(value, f"{name} checksum"))
            for name, value in checksums.items()
        },
        content_checksum=CanonicalDigest(
            _text(manifest_record.get("content_checksum"), "manifest content checksum")
        ),
        format_id=_text(manifest_record.get("format_id"), "manifest format ID"),
        format_version=_integer(manifest_record.get("format_version"), "manifest format version"),
    )
    return EvidenceBundle(manifest=manifest, evidence=evidence)


def parse_evidence_bundle(document: object) -> EvidenceBundle:
    try:
        return _parse_evidence_bundle(document)
    except EvidenceBundleError:
        raise
    except (TypeError, ValueError) as error:
        raise EvidenceBundleError(str(error)) from error


def load_evidence_bundle(path: Path) -> EvidenceBundle:
    try:
        with path.open("r", encoding="utf-8") as stream:
            return parse_evidence_bundle(json.load(stream))
    except (OSError, json.JSONDecodeError) as error:
        raise EvidenceBundleError(f"cannot read evidence bundle {path}: {error}") from error


def write_evidence_bundle(path: Path, bundle: EvidenceBundle) -> None:
    """Atomically publish a new bundle and never overwrite an existing path."""

    destination = path.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=destination.parent,
        prefix=f".{destination.name}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(
                evidence_bundle_document(bundle),
                stream,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, destination)
        directory_descriptor = os.open(destination.parent, os.O_RDONLY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    except FileExistsError as error:
        raise EvidenceBundleError(
            f"refusing to overwrite evidence bundle: {destination}"
        ) from error
    except OSError as error:
        raise EvidenceBundleError(f"cannot write evidence bundle {destination}: {error}") from error
    finally:
        temporary.unlink(missing_ok=True)
