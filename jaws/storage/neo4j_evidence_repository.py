"""Atomic Neo4j export/import adapter for portable evidence snapshots."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping, Sequence
from datetime import datetime
from typing import Any, Protocol, Self, TypeVar, cast

from jaws.domain import (
    ArchivedEntity,
    ArchivedObservationScope,
    ArchivedProfile,
    CaptureId,
    EntityId,
    EvidenceSchemaProvenance,
    EvidenceSnapshot,
    ObservationScopeId,
    ObservationScopeKind,
    OutlierStatus,
    ProfileStatus,
    SchemaMigrationProvenance,
    canonical_digest,
    canonical_json,
    utc_text,
)
from jaws.ports import EvidenceImportConflictError, EvidenceSchemaConflictError

from .migrations import manager
from .neo4j_profile_repositories import Neo4jEnrichmentRepository
from .neo4j_repositories import Neo4jCaptureRepository, Neo4jPacketRepository

ResultT = TypeVar("ResultT")


class _Record(Protocol):
    def __getitem__(self, key: str) -> Any: ...


class _Result(Protocol):
    def __iter__(self) -> Iterator[_Record]: ...

    def consume(self) -> Any: ...


class _Transaction(Protocol):
    def run(self, query: str, parameters: Mapping[str, object] | None = None) -> _Result: ...


class _Session(Protocol):
    def __enter__(self) -> Self: ...

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None: ...

    def run(self, query: str, parameters: Mapping[str, object] | None = None) -> _Result: ...

    def execute_write(self, work: Callable[[_Transaction], ResultT]) -> ResultT: ...


class _Driver(Protocol):
    def session(self, *, database: str) -> _Session: ...


_SCHEMA_QUERY = """
MATCH (migration:JAWS_SCHEMA_MIGRATION)
RETURN migration.VERSION AS version,
       migration.NAME AS name,
       migration.CHECKSUM AS checksum
ORDER BY version
"""

_SCOPES_QUERY = """
MATCH (scope:OBSERVATION_SCOPE)
OPTIONAL MATCH (scope)-[:INCLUDES]->(capture:CAPTURE)
RETURN scope.SCOPE_ID AS scope_id,
       scope.KIND AS kind,
       toString(scope.CREATED_AT) AS created_at,
       scope.PERSPECTIVE_IP AS perspective,
       scope.FILTERS AS filters,
       coalesce(scope.QUARANTINED, false) AS quarantined,
       [value IN collect(capture.CAPTURE_ID) WHERE value IS NOT NULL] AS capture_ids
ORDER BY scope_id
"""

_PROFILES_QUERY = """
MATCH (endpoint:ENDPOINT)
WHERE endpoint.SCOPE_ID IS NOT NULL AND endpoint.IP_ADDRESS IS NOT NULL
RETURN endpoint.SCOPE_ID AS scope_id,
       endpoint.IP_ADDRESS AS entity_address,
       endpoint.PROFILE_KEY AS profile_key,
       endpoint.CAPTURE_ID AS legacy_scope,
       endpoint.REPRESENTATION_ID AS representation_id,
       endpoint.REPRESENTATION_VERSION AS representation_version,
       endpoint.MODEL_ID AS model_id,
       endpoint.MODEL_REVISION AS model_revision,
       toString(endpoint.TIMESTAMP) AS computed_at,
       endpoint.ENDPOINT_TYPE AS address_classification,
       endpoint.ORGANIZATION AS organization,
       endpoint.HOSTNAME AS hostname,
       endpoint.LOCATION AS location,
       endpoint.BYTES_OUT AS bytes_out,
       endpoint.PACKETS_OUT AS packets_out,
       endpoint.OUT_PEERS AS out_peers,
       endpoint.OUT_PORTS AS out_ports,
       endpoint.BYTES_IN AS bytes_in,
       endpoint.PACKETS_IN AS packets_in,
       endpoint.IN_PEERS AS in_peers,
       endpoint.IN_PORTS AS in_ports,
       endpoint.PROTOCOLS AS protocols,
       endpoint.INTERVAL_MEAN AS interval_mean,
       endpoint.INTERVAL_CV AS interval_cv,
       endpoint.EMBEDDING AS embedding,
       endpoint.OUTLIER_STATUS AS outlier_status,
       endpoint.OUTLIER AS legacy_outlier,
       endpoint.PROFILE_STATUS AS profile_status
ORDER BY scope_id, entity_address, profile_key
"""

_ENTITIES_QUERY = """
MATCH (entity:IP_ADDRESS)
OPTIONAL MATCH (organization:ORGANIZATION)-[:OWNERSHIP]->(entity)
RETURN entity.IP_ADDRESS AS ip_address,
       entity.HOSTNAME AS hostname,
       entity.LOCATION AS location,
       entity.COORDINATES AS coordinates,
       [value IN collect(DISTINCT organization.ORGANIZATION)
        WHERE value IS NOT NULL] AS organizations
ORDER BY ip_address
"""

_EVIDENCE_COUNT_QUERY = """
MATCH (node)
WHERE NOT node:JAWS_SCHEMA_MIGRATION
  AND NOT node:JAWS_AUDIT_EVENT
  AND NOT (
      node:OBSERVATION_SCOPE
      AND node.SCOPE_ID IN ['scope_pooled_all', 'scope_legacy_unstamped']
  )
RETURN count(node) AS count
"""

_CREATE_ENTITIES = """
UNWIND $records AS record
CREATE (entity:IP_ADDRESS {
    IP_ADDRESS: record.ip_address,
    HOSTNAME: record.hostname,
    LOCATION: record.location,
    COORDINATES: record.coordinates
})
FOREACH (organization_name IN record.organizations |
    MERGE (organization:ORGANIZATION {ORGANIZATION: organization_name})
    CREATE (organization)-[:OWNERSHIP]->(entity)
)
"""

_CREATE_CAPTURES = """
UNWIND $records AS record
CREATE (:CAPTURE {
    CAPTURE_ID: record.capture_id,
    LEGACY_CAPTURE_ID: record.legacy_capture_id,
    STATE: record.state,
    SOURCE_KIND: record.source_kind,
    SOURCE_NAME: record.source_name,
    CONTENT_SHA256: record.content_sha256,
    REGISTERED_AT: datetime(record.registered_at),
    STARTED_AT: CASE WHEN record.started_at IS NULL THEN null ELSE datetime(record.started_at) END,
    ENDED_AT: CASE WHEN record.ended_at IS NULL THEN null ELSE datetime(record.ended_at) END,
    PACKET_COUNT: record.packet_count,
    PERSPECTIVE_IP: record.perspective_ip,
    CAPTURE_FILTER: record.capture_filter,
    TOOL_VERSIONS_JSON: record.tool_versions_json,
    FAILURE_CODE: record.failure_code,
    SOURCE: record.source_name,
    STARTED: CASE WHEN record.started_at IS NULL THEN null ELSE datetime(record.started_at) END,
    PACKETS: record.packet_count
})
"""

_CREATE_SCOPES = """
UNWIND $records AS record
CREATE (scope:OBSERVATION_SCOPE {
    SCOPE_ID: record.scope_id,
    KIND: record.kind,
    CREATED_AT: CASE WHEN record.created_at IS NULL THEN null ELSE datetime(record.created_at) END,
    PERSPECTIVE_IP: record.perspective,
    FILTERS: record.filters,
    QUARANTINED: record.quarantined
})
WITH scope, record
UNWIND record.capture_ids AS capture_id
MATCH (capture:CAPTURE {CAPTURE_ID: capture_id})
CREATE (scope)-[:INCLUDES]->(capture)
"""

_CREATE_PACKETS = """
UNWIND $records AS record
MATCH (source:IP_ADDRESS {IP_ADDRESS: record.source_ip})
MATCH (destination:IP_ADDRESS {IP_ADDRESS: record.destination_ip})
CREATE (packet:PACKET {
    PROTOCOL: record.protocol,
    SIZE: record.size_bytes,
    PAYLOAD: record.payload,
    TIMESTAMP: datetime(record.observed_at),
    CAPTURE_ID: record.capture_id,
    SRC_IP: record.source_ip,
    DST_IP: record.destination_ip,
    SRC_PORT: record.source_port,
    DST_PORT: record.destination_port
})
FOREACH (_ IN CASE WHEN record.source_port = 0 THEN [] ELSE [1] END |
    MERGE (source)-[:PORT]->(source_port:PORT {
        PORT: record.source_port, IP_ADDRESS: record.source_ip
    })
    CREATE (source_port)-[:SENT]->(packet)
)
FOREACH (_ IN CASE WHEN record.destination_port = 0 THEN [] ELSE [1] END |
    MERGE (destination)-[:PORT]->(destination_port:PORT {
        PORT: record.destination_port, IP_ADDRESS: record.destination_ip
    })
    CREATE (packet)-[:RECEIVED]->(destination_port)
)
"""

_RESTORE_ENRICHMENTS = """
UNWIND $records AS record
MATCH (entity:IP_ADDRESS {IP_ADDRESS: record.ip_address})
SET entity.ENRICHMENT_STATUS = record.status,
    entity.ENRICHED_AT = datetime(record.acquired_at),
    entity.ENRICHMENT_PROVIDER_ID = record.provider_id,
    entity.ENRICHMENT_PROVIDER_REVISION = record.provider_revision,
    entity.ENRICHMENT_ORGANIZATION = record.organization,
    entity.ENRICHMENT_ASN = record.asn,
    entity.ENRICHMENT_CONFIDENCE = record.confidence,
    entity.ENRICHMENT_FAILURE_CODE = record.failure_code,
    entity.HOSTNAME = coalesce(record.hostname, entity.HOSTNAME),
    entity.LOCATION = coalesce(record.location, entity.LOCATION),
    entity.COORDINATES = coalesce(record.coordinates, entity.COORDINATES)
"""

_CREATE_ANNOTATIONS = """
UNWIND $records AS record
MATCH (entity:IP_ADDRESS {IP_ADDRESS: record.ip_address})
CREATE (annotation:ENTITY_ANNOTATION {
    ANNOTATION_KEY: record.annotation_key,
    KEY: record.key,
    VALUE: record.value,
    AUTHOR: record.author,
    RECORDED_AT: datetime(record.recorded_at),
    GROUND_TRUTH: record.ground_truth
})
CREATE (entity)-[:ANNOTATED_WITH]->(annotation)
"""

_CREATE_PROFILES = """
UNWIND $records AS record
MATCH (entity:IP_ADDRESS {IP_ADDRESS: record.ip_address})
CREATE (entity)-[:PROFILE]->(:ENDPOINT {
    PROFILE_KEY: record.profile_key,
    SCOPE_ID: record.scope_id,
    CAPTURE_ID: record.legacy_scope,
    IP_ADDRESS: record.ip_address,
    REPRESENTATION_ID: record.representation_id,
    REPRESENTATION_VERSION: record.representation_version,
    MODEL_ID: record.model_id,
    MODEL_REVISION: record.model_revision,
    TIMESTAMP: CASE WHEN record.computed_at IS NULL THEN null ELSE datetime(record.computed_at) END,
    ENDPOINT_TYPE: record.address_classification,
    ORGANIZATION: record.organization,
    HOSTNAME: record.hostname,
    LOCATION: record.location,
    BYTES_OUT: record.bytes_out,
    PACKETS_OUT: record.packets_out,
    OUT_PEERS: record.out_peers,
    OUT_PORTS: record.out_ports,
    BYTES_IN: record.bytes_in,
    PACKETS_IN: record.packets_in,
    IN_PEERS: record.in_peers,
    IN_PORTS: record.in_ports,
    PROTOCOLS: record.protocols,
    INTERVAL_MEAN: record.interval_mean,
    INTERVAL_CV: record.interval_cv,
    EMBEDDING: record.embedding,
    OUTLIER_STATUS: record.outlier_status,
    OUTLIER: record.legacy_outlier,
    PROFILE_STATUS: record.profile_status
})
"""


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise EvidenceImportConflictError(f"stored evidence is missing {name}")
    return value


def _optional_text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _optional_datetime(value: object) -> datetime | None:
    text = _optional_text(value)
    return datetime.fromisoformat(text.replace("Z", "+00:00")) if text else None


def _integer(value: object) -> int:
    if value is None:
        return 0
    if not isinstance(value, int) or isinstance(value, bool):
        raise EvidenceImportConflictError("stored evidence count must be an integer")
    return value


def _float(value: object) -> float | None:
    if value is None:
        return None
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise EvidenceImportConflictError("stored evidence measurement must be numeric")
    return float(value)


def _strings(value: object) -> tuple[str, ...]:
    return tuple(str(item) for item in value) if isinstance(value, list) else ()


def _integers(value: object) -> tuple[int, ...]:
    return tuple(int(item) for item in value) if isinstance(value, list) else ()


def _floats(value: object) -> tuple[float, ...]:
    return tuple(float(item) for item in value) if isinstance(value, list) else ()


def _outlier(status: object, legacy: object) -> OutlierStatus:
    if isinstance(status, str):
        return OutlierStatus(status)
    if legacy is True:
        return OutlierStatus.OUTLIER
    if legacy is False:
        return OutlierStatus.INLIER
    return OutlierStatus.NOT_SCORED


def _chunks[T](values: Sequence[T], size: int = 1000) -> Iterator[Sequence[T]]:
    for start in range(0, len(values), size):
        yield values[start : start + size]


def _schema(rows: Iterator[_Record]) -> EvidenceSchemaProvenance:
    migrations = tuple(
        SchemaMigrationProvenance(
            version=int(row["version"]),
            name=_text(row["name"], "migration name"),
            checksum=_text(row["checksum"], "migration checksum"),
        )
        for row in rows
    )
    if not migrations:
        raise EvidenceSchemaConflictError("evidence database has no managed schema history")
    return EvidenceSchemaProvenance(version=migrations[-1].version, migrations=migrations)


def _empty(store: _Session | _Transaction) -> bool:
    row = next(iter(store.run(_EVIDENCE_COUNT_QUERY)), None)
    return row is not None and int(row["count"]) == 0


class Neo4jEvidenceRepository:
    """Exact managed-evidence snapshots with empty-target atomic restore."""

    def __init__(self, driver: object, database: str) -> None:
        if not database.strip():
            raise ValueError("database name cannot be empty")
        self._driver = cast(_Driver, driver)
        self.database = database
        self._captures = Neo4jCaptureRepository(driver, database)
        self._packets = Neo4jPacketRepository(driver, database)
        self._enrichment = Neo4jEnrichmentRepository(driver, database)

    def schema_provenance(self) -> EvidenceSchemaProvenance:
        status = manager(self._driver, self.database).validate()
        blocking = tuple(
            issue for issue in status.issues if not issue.startswith("pending migration")
        )
        if blocking or status.current_version is None:
            raise EvidenceSchemaConflictError(
                "evidence schema cannot be exported safely: "
                + "; ".join(blocking or ("no managed schema history",))
            )
        return EvidenceSchemaProvenance(
            version=status.current_version,
            migrations=tuple(
                SchemaMigrationProvenance(item.version, item.name, item.checksum)
                for item in status.applied
                if item.version <= status.current_version
            ),
        )

    def snapshot(self) -> EvidenceSnapshot:
        schema = self.schema_provenance()
        captures = self._captures.list_all()
        packets = self._packets.read_all()
        with self._driver.session(database=self.database) as session:
            stored_entities = tuple(self._entity(row) for row in session.run(_ENTITIES_QUERY))
            scopes = tuple(self._scope(row) for row in session.run(_SCOPES_QUERY))
            profiles = tuple(self._profile(row) for row in session.run(_PROFILES_QUERY))
        entity_by_id = {item.entity_id: item for item in stored_entities}
        referenced_addresses = {
            address for packet in packets for address in (packet.source_ip, packet.destination_ip)
        } | {profile.entity_id.value.removeprefix("ip:") for profile in profiles}
        for address in referenced_addresses:
            entity_id = EntityId(f"ip:{address}")
            entity_by_id.setdefault(entity_id, ArchivedEntity(entity_id, address))
        entities = tuple(entity_by_id.values())
        enrichments = tuple(
            record
            for entity in entities
            if (record := self._enrichment.get(entity.entity_id)) is not None
        )
        annotations = tuple(
            annotation
            for entity in entities
            for annotation in self._enrichment.annotations(entity.entity_id)
        )
        return EvidenceSnapshot(
            schema=schema,
            captures=captures,
            observation_scopes=scopes,
            packets=packets,
            entities=entities,
            enrichments=enrichments,
            annotations=annotations,
            profiles=profiles,
        )

    @staticmethod
    def _entity(row: _Record) -> ArchivedEntity:
        address = _text(row["ip_address"], "IP_ADDRESS")
        return ArchivedEntity(
            entity_id=EntityId(f"ip:{address}"),
            ip_address=address,
            organizations=_strings(row["organizations"]),
            hostname=_optional_text(row["hostname"]),
            location=_optional_text(row["location"]),
            coordinates=_optional_text(row["coordinates"]),
        )

    @staticmethod
    def _scope(row: _Record) -> ArchivedObservationScope:
        kind = _optional_text(row["kind"])
        perspective = _optional_text(row["perspective"])
        return ArchivedObservationScope(
            scope_id=ObservationScopeId(_text(row["scope_id"], "SCOPE_ID")),
            kind=ObservationScopeKind(kind) if kind else None,
            created_at=_optional_datetime(row["created_at"]),
            capture_ids=tuple(CaptureId(value) for value in _strings(row["capture_ids"])),
            perspective=EntityId(perspective) if perspective else None,
            filters=_strings(row["filters"]),
            quarantined=bool(row["quarantined"]),
        )

    @staticmethod
    def _profile(row: _Record) -> ArchivedProfile:
        status = _optional_text(row["profile_status"])
        return ArchivedProfile(
            scope_id=ObservationScopeId(_text(row["scope_id"], "SCOPE_ID")),
            entity_id=EntityId(f"ip:{_text(row['entity_address'], 'IP_ADDRESS')}"),
            profile_key=_optional_text(row["profile_key"]),
            legacy_scope=_optional_text(row["legacy_scope"]),
            representation_id=_optional_text(row["representation_id"]),
            representation_version=_optional_text(row["representation_version"]),
            model_id=_optional_text(row["model_id"]),
            model_revision=_optional_text(row["model_revision"]),
            computed_at=_optional_datetime(row["computed_at"]),
            address_classification=_optional_text(row["address_classification"]),
            organization=_optional_text(row["organization"]),
            hostname=_optional_text(row["hostname"]),
            location=_optional_text(row["location"]),
            bytes_out=_integer(row["bytes_out"]),
            packets_out=_integer(row["packets_out"]),
            out_peers=_integer(row["out_peers"]),
            out_ports=_integers(row["out_ports"]),
            bytes_in=_integer(row["bytes_in"]),
            packets_in=_integer(row["packets_in"]),
            in_peers=_integer(row["in_peers"]),
            in_ports=_integers(row["in_ports"]),
            protocols=_strings(row["protocols"]),
            interval_mean=_float(row["interval_mean"]),
            interval_cv=_float(row["interval_cv"]),
            embedding=_floats(row["embedding"]),
            outlier=_outlier(row["outlier_status"], row["legacy_outlier"]),
            status=ProfileStatus(status) if status else ProfileStatus.LEGACY_UNVERSIONED,
        )

    def is_empty(self) -> bool:
        with self._driver.session(database=self.database) as session:
            return _empty(session)

    def restore(self, evidence: EvidenceSnapshot) -> None:
        expected_schema = self.schema_provenance()
        if evidence.schema != expected_schema:
            raise EvidenceSchemaConflictError("evidence schema does not match import target")

        def write(transaction: _Transaction) -> None:
            if _schema(iter(transaction.run(_SCHEMA_QUERY))) != evidence.schema:
                raise EvidenceSchemaConflictError(
                    "evidence schema changed after import was planned"
                )
            if not _empty(transaction):
                raise EvidenceImportConflictError("evidence import target is not empty")
            transaction.run(
                "MATCH (scope:OBSERVATION_SCOPE) "
                "WHERE scope.SCOPE_ID IN ['scope_pooled_all', 'scope_legacy_unstamped'] "
                "DETACH DELETE scope"
            ).consume()
            self._write_records(transaction, _CREATE_ENTITIES, self._entity_values(evidence))
            self._write_records(transaction, _CREATE_CAPTURES, self._capture_values(evidence))
            self._write_records(transaction, _CREATE_SCOPES, self._scope_values(evidence))
            self._write_records(transaction, _CREATE_PACKETS, self._packet_values(evidence))
            self._write_records(
                transaction, _RESTORE_ENRICHMENTS, self._enrichment_values(evidence)
            )
            self._write_records(transaction, _CREATE_ANNOTATIONS, self._annotation_values(evidence))
            self._write_records(transaction, _CREATE_PROFILES, self._profile_values(evidence))

        with self._driver.session(database=self.database) as session:
            session.execute_write(write)

    @staticmethod
    def _write_records(
        transaction: _Transaction, query: str, records: Sequence[dict[str, object]]
    ) -> None:
        for batch in _chunks(records):
            transaction.run(query, {"records": list(batch)}).consume()

    @staticmethod
    def _entity_values(evidence: EvidenceSnapshot) -> list[dict[str, object]]:
        return [
            {
                "ip_address": item.ip_address,
                "organizations": list(item.organizations),
                "hostname": item.hostname,
                "location": item.location,
                "coordinates": item.coordinates,
            }
            for item in evidence.entities
        ]

    @staticmethod
    def _capture_values(evidence: EvidenceSnapshot) -> list[dict[str, object]]:
        return [
            {
                "capture_id": item.capture_id.value,
                "legacy_capture_id": item.legacy_capture_id,
                "state": item.state.value,
                "source_kind": item.source_kind.value,
                "source_name": item.source_name,
                "content_sha256": str(item.content_digest) if item.content_digest else None,
                "registered_at": utc_text(item.registered_at),
                "started_at": utc_text(item.started_at) if item.started_at else None,
                "ended_at": utc_text(item.ended_at) if item.ended_at else None,
                "packet_count": item.packet_count,
                "perspective_ip": item.perspective.value if item.perspective else None,
                "capture_filter": item.capture_filter,
                "tool_versions_json": canonical_json(item.tool_versions),
                "failure_code": item.failure_code,
            }
            for item in evidence.captures
        ]

    @staticmethod
    def _scope_values(evidence: EvidenceSnapshot) -> list[dict[str, object]]:
        return [
            {
                "scope_id": item.scope_id.value,
                "kind": item.kind.value if item.kind else None,
                "created_at": utc_text(item.created_at) if item.created_at else None,
                "capture_ids": [capture_id.value for capture_id in item.capture_ids],
                "perspective": item.perspective.value if item.perspective else None,
                "filters": list(item.filters),
                "quarantined": item.quarantined,
            }
            for item in evidence.observation_scopes
        ]

    @staticmethod
    def _packet_values(evidence: EvidenceSnapshot) -> list[dict[str, object]]:
        return [
            {
                "capture_id": item.capture_id.value,
                "observed_at": utc_text(item.observed_at),
                "protocol": item.protocol,
                "size_bytes": item.size_bytes,
                "source_ip": item.source_ip,
                "destination_ip": item.destination_ip,
                "source_port": item.source_port or 0,
                "destination_port": item.destination_port or 0,
                "payload": item.payload,
            }
            for item in evidence.packets
        ]

    @staticmethod
    def _enrichment_values(evidence: EvidenceSnapshot) -> list[dict[str, object]]:
        return [
            {
                "ip_address": item.ip_address,
                "status": item.status.value,
                "acquired_at": utc_text(item.acquired_at),
                "provider_id": item.provider_id,
                "provider_revision": item.provider_revision,
                "organization": item.organization,
                "asn": item.asn,
                "hostname": item.hostname,
                "location": item.location,
                "coordinates": item.coordinates,
                "confidence": item.confidence,
                "failure_code": item.failure_code,
            }
            for item in evidence.enrichments
        ]

    @staticmethod
    def _annotation_values(evidence: EvidenceSnapshot) -> list[dict[str, object]]:
        return [
            {
                "ip_address": item.entity_id.value.removeprefix("ip:"),
                "annotation_key": "annotation_"
                + str(canonical_digest({"entity_id": item.entity_id, "key": item.key})),
                "key": item.key,
                "value": item.value,
                "author": item.author,
                "recorded_at": utc_text(item.recorded_at),
                "ground_truth": item.ground_truth,
            }
            for item in evidence.annotations
        ]

    @staticmethod
    def _profile_values(evidence: EvidenceSnapshot) -> list[dict[str, object]]:
        return [
            {
                "profile_key": item.profile_key,
                "scope_id": item.scope_id.value,
                "legacy_scope": item.legacy_scope,
                "ip_address": item.entity_id.value.removeprefix("ip:"),
                "representation_id": item.representation_id,
                "representation_version": item.representation_version,
                "model_id": item.model_id,
                "model_revision": item.model_revision,
                "computed_at": utc_text(item.computed_at) if item.computed_at else None,
                "address_classification": item.address_classification,
                "organization": item.organization,
                "hostname": item.hostname,
                "location": item.location,
                "bytes_out": item.bytes_out,
                "packets_out": item.packets_out,
                "out_peers": item.out_peers,
                "out_ports": list(item.out_ports),
                "bytes_in": item.bytes_in,
                "packets_in": item.packets_in,
                "in_peers": item.in_peers,
                "in_ports": list(item.in_ports),
                "protocols": list(item.protocols),
                "interval_mean": item.interval_mean,
                "interval_cv": item.interval_cv,
                "embedding": list(item.embedding),
                "outlier_status": item.outlier.value,
                "legacy_outlier": (
                    True
                    if item.outlier is OutlierStatus.OUTLIER
                    else False
                    if item.outlier is OutlierStatus.INLIER
                    else None
                ),
                "profile_status": item.status.value,
            }
            for item in evidence.profiles
        ]
