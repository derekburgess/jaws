"""Neo4j implementations of capture and packet repository contracts."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, Protocol, Self, TypeVar, cast

from jaws.domain import (
    ACTIVE_CAPTURE_STATES,
    CAPTURE_TRANSITIONS,
    CanonicalDigest,
    CaptureId,
    CaptureRecord,
    CaptureSourceKind,
    CaptureSourceMetadata,
    CaptureState,
    EntityId,
    ObservationScope,
    ObservationWindow,
    PacketRecord,
    canonical_json,
    require_transition,
    utc_text,
)
from jaws.ports import (
    CaptureNotFoundError,
    CaptureStateConflictError,
    DuplicateCaptureError,
    InactiveCaptureError,
    RepositorySchemaError,
)
from jaws.ports.repositories import capture_metadata

from .migrations import manager
from .neo4j_experiment_index_repository import Neo4jExperimentIndexRepository
from .neo4j_finding_repository import Neo4jFindingRepository
from .neo4j_inspection_repository import Neo4jInspectionRepository
from .neo4j_profile_repositories import Neo4jEnrichmentRepository, Neo4jProfileRepository

if TYPE_CHECKING:
    from .neo4j_administration_repository import Neo4jAdministrationRepository
    from .neo4j_evidence_repository import Neo4jEvidenceRepository

ResultT = TypeVar("ResultT")


class _Record(Protocol):
    def __getitem__(self, key: str) -> Any: ...


class _Result(Protocol):
    def __iter__(self) -> Iterator[_Record]: ...

    def single(self) -> _Record | None: ...

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


_CAPTURE_FIELDS = """
capture.CAPTURE_ID AS capture_id,
capture.LEGACY_CAPTURE_ID AS legacy_capture_id,
capture.STATE AS state,
capture.SOURCE_KIND AS source_kind,
capture.SOURCE_NAME AS source_name,
toString(capture.REGISTERED_AT) AS registered_at,
toString(capture.STARTED_AT) AS started_at,
toString(capture.ENDED_AT) AS ended_at,
capture.PACKET_COUNT AS packet_count,
capture.CONTENT_SHA256 AS content_sha256,
capture.SOURCE_FILE_NAME AS source_file_name,
capture.SOURCE_SIZE_BYTES AS source_size_bytes,
capture.SOURCE_LOCATOR AS source_locator,
capture.SOURCE_LOCATOR_PORTABLE AS source_locator_portable,
capture.PERSPECTIVE_IP AS perspective_ip,
capture.CAPTURE_FILTER AS capture_filter,
capture.TOOL_VERSIONS_JSON AS tool_versions_json,
capture.FAILURE_CODE AS failure_code
"""

_ADD_CAPTURE_QUERY = """
CREATE (capture:CAPTURE {
    CAPTURE_ID: $capture_id,
    LEGACY_CAPTURE_ID: $legacy_capture_id,
    STATE: $state,
    SOURCE_KIND: $source_kind,
    SOURCE_NAME: $source_name,
    CONTENT_SHA256: $content_sha256,
    SOURCE_FILE_NAME: $source_file_name,
    SOURCE_SIZE_BYTES: $source_size_bytes,
    SOURCE_LOCATOR: $source_locator,
    SOURCE_LOCATOR_PORTABLE: $source_locator_portable,
    REGISTERED_AT: datetime($registered_at),
    STARTED_AT: datetime($started_at),
    STARTED: datetime($started_at),
    PACKET_COUNT: $packet_count,
    PACKETS: $packet_count,
    PERSPECTIVE_IP: $perspective_ip,
    CAPTURE_FILTER: $capture_filter,
    TOOL_VERSIONS_JSON: $tool_versions_json,
    SOURCE: $source_name
})
CREATE (scope:OBSERVATION_SCOPE {
    SCOPE_ID: $scope_id,
    KIND: $scope_kind,
    CREATED_AT: datetime($registered_at),
    PERSPECTIVE_IP: $perspective_ip,
    FILTERS: $filters
})
CREATE (scope)-[:INCLUDES]->(capture)
"""

_GET_CAPTURE_QUERY = f"""
MATCH (capture:CAPTURE {{CAPTURE_ID: $capture_id}})
RETURN {_CAPTURE_FIELDS}
"""

_FIND_LEGACY_QUERY = f"""
MATCH (capture:CAPTURE {{LEGACY_CAPTURE_ID: $legacy_capture_id}})
RETURN {_CAPTURE_FIELDS}
ORDER BY capture.REGISTERED_AT, capture.CAPTURE_ID
"""

_LIST_CAPTURES_QUERY = f"""
MATCH (capture:CAPTURE)
WHERE capture.CAPTURE_ID IS NOT NULL
RETURN {_CAPTURE_FIELDS}
ORDER BY capture.REGISTERED_AT, capture.CAPTURE_ID
"""

_TRANSITION_CAPTURE_QUERY = """
MATCH (capture:CAPTURE {CAPTURE_ID: $capture_id})
WHERE capture.STATE = $expected_state
SET capture.STATE = $state,
    capture.STARTED_AT = CASE
        WHEN $started_at IS NULL THEN capture.STARTED_AT
        ELSE datetime($started_at)
    END,
    capture.STARTED = CASE
        WHEN $started_at IS NULL THEN capture.STARTED
        ELSE datetime($started_at)
    END,
    capture.ENDED_AT = CASE
        WHEN $ended_at IS NULL THEN capture.ENDED_AT
        ELSE datetime($ended_at)
    END,
    capture.PACKET_COUNT = $packet_count,
    capture.PACKETS = $packet_count,
    capture.FAILURE_CODE = $failure_code
RETURN capture.CAPTURE_ID AS capture_id
"""

_CAPTURE_STATE_QUERY = """
MATCH (capture:CAPTURE {CAPTURE_ID: $capture_id})
RETURN capture.STATE AS state
"""

_APPEND_PACKETS_QUERY = """
UNWIND $packets AS packet
MERGE (source:IP_ADDRESS {IP_ADDRESS: packet.source_ip})
MERGE (destination:IP_ADDRESS {IP_ADDRESS: packet.destination_ip})
CREATE (stored:PACKET {
    PROTOCOL: packet.protocol,
    SIZE: packet.size_bytes,
    PAYLOAD: packet.payload,
    TIMESTAMP: datetime(packet.observed_at),
    CAPTURE_ID: $capture_id,
    SRC_IP: packet.source_ip,
    DST_IP: packet.destination_ip,
    SRC_PORT: packet.source_port,
    DST_PORT: packet.destination_port
})
FOREACH (_ IN CASE WHEN packet.source_port <> 0 THEN [1] ELSE [] END |
    MERGE (source)-[:PORT]->(source_port:PORT {
        PORT: packet.source_port,
        IP_ADDRESS: packet.source_ip
    })
    CREATE (source_port)-[:SENT]->(stored)
)
FOREACH (_ IN CASE WHEN packet.destination_port <> 0 THEN [1] ELSE [] END |
    MERGE (destination)-[:PORT]->(destination_port:PORT {
        PORT: packet.destination_port,
        IP_ADDRESS: packet.destination_ip
    })
    CREATE (stored)-[:RECEIVED]->(destination_port)
)
"""

_READ_PACKETS_QUERY = """
MATCH (packet:PACKET)
WHERE packet.CAPTURE_ID IN $capture_ids
  AND ($started_at IS NULL OR packet.TIMESTAMP >= datetime($started_at))
  AND ($ended_at IS NULL OR packet.TIMESTAMP <= datetime($ended_at))
RETURN packet.CAPTURE_ID AS capture_id,
       toString(packet.TIMESTAMP) AS observed_at,
       packet.PROTOCOL AS protocol,
       packet.SIZE AS size_bytes,
       packet.SRC_IP AS source_ip,
       packet.DST_IP AS destination_ip,
       packet.SRC_PORT AS source_port,
       packet.DST_PORT AS destination_port,
       packet.PAYLOAD AS payload
ORDER BY packet.TIMESTAMP, packet.CAPTURE_ID, elementId(packet)
"""

_READ_ALL_PACKETS_QUERY = """
MATCH (packet:PACKET)
RETURN packet.CAPTURE_ID AS capture_id,
       toString(packet.TIMESTAMP) AS observed_at,
       packet.PROTOCOL AS protocol,
       packet.SIZE AS size_bytes,
       packet.SRC_IP AS source_ip,
       packet.DST_IP AS destination_ip,
       packet.SRC_PORT AS source_port,
       packet.DST_PORT AS destination_port,
       packet.PAYLOAD AS payload
ORDER BY packet.TIMESTAMP, packet.CAPTURE_ID, elementId(packet)
"""


def _required_text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise RepositorySchemaError(f"stored capture is missing {field_name}")
    return value


def _optional_text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _payload(value: object) -> str | None:
    if value is None or isinstance(value, str):
        return value
    raise RepositorySchemaError("stored packet payload must be text or null")


def _packet_from_record(row: _Record) -> PacketRecord:
    return PacketRecord(
        capture_id=CaptureId(_required_text(row["capture_id"], "CAPTURE_ID")),
        observed_at=_datetime(row["observed_at"], "TIMESTAMP"),
        protocol=_required_text(row["protocol"], "PROTOCOL"),
        size_bytes=int(row["size_bytes"]),
        source_ip=_required_text(row["source_ip"], "SRC_IP"),
        destination_ip=_required_text(row["destination_ip"], "DST_IP"),
        source_port=_stored_port(row["source_port"]),
        destination_port=_stored_port(row["destination_port"]),
        payload=_payload(row["payload"]),
    )


def _stored_port(value: object) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool):
        raise RepositorySchemaError("stored packet port must be an integer or null")
    return value or None


def _datetime(value: object, field_name: str) -> datetime:
    return datetime.fromisoformat(_required_text(value, field_name).replace("Z", "+00:00"))


def _optional_datetime(value: object) -> datetime | None:
    text = _optional_text(value)
    return datetime.fromisoformat(text.replace("Z", "+00:00")) if text else None


def _tool_versions(value: object) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, str):
        raise RepositorySchemaError("stored tool versions must be canonical JSON text")
    decoded = json.loads(value)
    if not isinstance(decoded, dict) or any(
        not isinstance(key, str) or not isinstance(item, str) for key, item in decoded.items()
    ):
        raise RepositorySchemaError("stored tool versions must be a string map")
    return cast(dict[str, str], decoded)


def _source_metadata(row: _Record) -> CaptureSourceMetadata | None:
    file_name = _optional_text(row["source_file_name"])
    size_bytes = row["source_size_bytes"]
    locator = _optional_text(row["source_locator"])
    portable = row["source_locator_portable"]
    values = (file_name, size_bytes, locator, portable)
    if all(value is None for value in values):
        return None
    if file_name is None or not isinstance(size_bytes, int) or isinstance(size_bytes, bool):
        raise RepositorySchemaError("stored capture source metadata is incomplete")
    if not isinstance(portable, bool):
        raise RepositorySchemaError("stored capture source locator portability must be boolean")
    try:
        return CaptureSourceMetadata(file_name, size_bytes, locator, portable)
    except ValueError as error:
        raise RepositorySchemaError("stored capture source metadata is invalid") from error


def _capture_from_record(row: _Record) -> CaptureRecord:
    digest = _optional_text(row["content_sha256"])
    perspective = _optional_text(row["perspective_ip"])
    return CaptureRecord(
        capture_id=CaptureId(_required_text(row["capture_id"], "CAPTURE_ID")),
        source_kind=CaptureSourceKind(_required_text(row["source_kind"], "SOURCE_KIND")),
        source_name=_required_text(row["source_name"], "SOURCE_NAME"),
        state=CaptureState(_required_text(row["state"], "STATE")),
        registered_at=_datetime(row["registered_at"], "REGISTERED_AT"),
        legacy_capture_id=_optional_text(row["legacy_capture_id"]),
        started_at=_optional_datetime(row["started_at"]),
        ended_at=_optional_datetime(row["ended_at"]),
        packet_count=int(row["packet_count"] or 0),
        content_digest=CanonicalDigest(digest) if digest else None,
        source_metadata=_source_metadata(row),
        perspective=EntityId(perspective) if perspective else None,
        capture_filter=_optional_text(row["capture_filter"]),
        tool_versions=_tool_versions(row["tool_versions_json"]),
        failure_code=_optional_text(row["failure_code"]),
    )


def _capture_parameters(record: CaptureRecord) -> dict[str, object]:
    scope = ObservationScope.for_capture(
        record.capture_id,
        record.registered_at,
        perspective=record.perspective,
        filters=(record.capture_filter,) if record.capture_filter else (),
    )
    return {
        "capture_id": record.capture_id.value,
        "legacy_capture_id": record.legacy_capture_id,
        "state": record.state.value,
        "source_kind": record.source_kind.value,
        "source_name": record.source_name,
        "content_sha256": str(record.content_digest) if record.content_digest else None,
        "source_file_name": (record.source_metadata.file_name if record.source_metadata else None),
        "source_size_bytes": (
            record.source_metadata.size_bytes if record.source_metadata else None
        ),
        "source_locator": (
            record.source_metadata.source_locator if record.source_metadata else None
        ),
        "source_locator_portable": (
            record.source_metadata.locator_portable if record.source_metadata else None
        ),
        "registered_at": utc_text(record.registered_at),
        "started_at": utc_text(record.started_at) if record.started_at else None,
        "packet_count": record.packet_count,
        "perspective_ip": record.perspective.value if record.perspective else None,
        "capture_filter": record.capture_filter,
        "tool_versions_json": canonical_json(record.tool_versions),
        "scope_id": scope.scope_id.value,
        "scope_kind": scope.kind.value,
        "filters": list(scope.filters),
    }


def _is_constraint_error(error: Exception) -> bool:
    code = str(getattr(error, "code", ""))
    return "Constraint" in type(error).__name__ or "Constraint" in code


class Neo4jCaptureRepository:
    """Neo4j capture catalog using atomic optimistic lifecycle transitions."""

    def __init__(self, driver: object, database: str) -> None:
        if not database.strip():
            raise ValueError("database name cannot be empty")
        self._driver = cast(_Driver, driver)
        self.database = database

    def add(self, record: CaptureRecord) -> None:
        try:
            with self._driver.session(database=self.database) as session:
                session.execute_write(
                    lambda transaction: transaction.run(
                        _ADD_CAPTURE_QUERY, _capture_parameters(record)
                    ).consume()
                )
        except Exception as error:
            if _is_constraint_error(error):
                raise DuplicateCaptureError(
                    f"capture already exists: {record.capture_id}"
                ) from error
            raise

    def get(self, capture_id: CaptureId) -> CaptureRecord | None:
        with self._driver.session(database=self.database) as session:
            row = session.run(_GET_CAPTURE_QUERY, {"capture_id": capture_id.value}).single()
        return _capture_from_record(row) if row is not None else None

    def find_by_legacy_id(self, legacy_capture_id: str) -> tuple[CaptureRecord, ...]:
        identity = legacy_capture_id.strip()
        if not identity:
            raise ValueError("legacy capture ID cannot be empty")
        with self._driver.session(database=self.database) as session:
            rows = session.run(_FIND_LEGACY_QUERY, {"legacy_capture_id": identity})
            return tuple(_capture_from_record(row) for row in rows)

    def list_all(self) -> tuple[CaptureRecord, ...]:
        with self._driver.session(database=self.database) as session:
            return tuple(_capture_from_record(row) for row in session.run(_LIST_CAPTURES_QUERY))

    def transition(self, record: CaptureRecord, *, expected_state: CaptureState) -> None:
        existing = self.get(record.capture_id)
        if existing is None:
            raise CaptureNotFoundError(f"capture does not exist: {record.capture_id}")
        if existing.state is not expected_state:
            raise CaptureStateConflictError(
                f"capture {record.capture_id} is {existing.state}, expected {expected_state}"
            )
        require_transition(expected_state, record.state, CAPTURE_TRANSITIONS)
        if capture_metadata(existing) != capture_metadata(record):
            raise ValueError("capture metadata cannot change during a lifecycle transition")
        parameters: dict[str, object] = {
            "capture_id": record.capture_id.value,
            "expected_state": expected_state.value,
            "state": record.state.value,
            "started_at": utc_text(record.started_at) if record.started_at else None,
            "ended_at": utc_text(record.ended_at) if record.ended_at else None,
            "packet_count": record.packet_count,
            "failure_code": record.failure_code,
        }
        with self._driver.session(database=self.database) as session:
            row = session.execute_write(
                lambda transaction: transaction.run(_TRANSITION_CAPTURE_QUERY, parameters).single()
            )
        if row is None:
            current = self.get(record.capture_id)
            if current is None:
                raise CaptureNotFoundError(f"capture does not exist: {record.capture_id}")
            raise CaptureStateConflictError(
                f"capture {record.capture_id} changed from expected state {expected_state}"
            )


class Neo4jPacketRepository:
    """Neo4j packet evidence with one transaction per bounded append batch."""

    def __init__(self, driver: object, database: str) -> None:
        if not database.strip():
            raise ValueError("database name cannot be empty")
        self._driver = cast(_Driver, driver)
        self.database = database

    def append(self, capture_id: CaptureId, records: Sequence[PacketRecord]) -> int:
        batch = tuple(records)
        if not batch:
            return 0
        if any(record.capture_id != capture_id for record in batch):
            raise ValueError("every packet in a batch must belong to the requested capture")
        packets: list[dict[str, object]] = [
            {
                "observed_at": utc_text(record.observed_at),
                "protocol": record.protocol,
                "size_bytes": record.size_bytes,
                "source_ip": record.source_ip,
                "destination_ip": record.destination_ip,
                "source_port": record.source_port or 0,
                "destination_port": record.destination_port or 0,
                "payload": record.payload,
            }
            for record in batch
        ]

        def write(transaction: _Transaction) -> int:
            capture = transaction.run(
                _CAPTURE_STATE_QUERY, {"capture_id": capture_id.value}
            ).single()
            if capture is None:
                raise CaptureNotFoundError(f"capture does not exist: {capture_id}")
            state = CaptureState(_required_text(capture["state"], "STATE"))
            if state not in ACTIVE_CAPTURE_STATES:
                raise InactiveCaptureError(f"cannot append packets to {state} capture")
            transaction.run(
                _APPEND_PACKETS_QUERY,
                {"capture_id": capture_id.value, "packets": packets},
            ).consume()
            return len(batch)

        with self._driver.session(database=self.database) as session:
            return session.execute_write(write)

    def read(self, window: ObservationWindow) -> tuple[PacketRecord, ...]:
        parameters: dict[str, object] = {
            "capture_ids": [capture_id.value for capture_id in window.capture_ids],
            "started_at": utc_text(window.started_at) if window.started_at else None,
            "ended_at": utc_text(window.ended_at) if window.ended_at else None,
        }
        with self._driver.session(database=self.database) as session:
            rows = session.run(_READ_PACKETS_QUERY, parameters)
            records = [_packet_from_record(row) for row in rows]
        positions = {capture_id: index for index, capture_id in enumerate(window.capture_ids)}
        return tuple(
            sorted(
                records,
                key=lambda record: (positions[record.capture_id], record.observed_at),
            )
        )

    def read_all(self) -> tuple[PacketRecord, ...]:
        with self._driver.session(database=self.database) as session:
            return tuple(_packet_from_record(row) for row in session.run(_READ_ALL_PACKETS_QUERY))


@dataclass(frozen=True, slots=True)
class Neo4jRepositories:
    """Validated capture/packet adapter set for one managed Neo4j database."""

    captures: Neo4jCaptureRepository
    packets: Neo4jPacketRepository
    enrichment: Neo4jEnrichmentRepository
    profiles: Neo4jProfileRepository
    inspection: Neo4jInspectionRepository
    evidence: Neo4jEvidenceRepository
    administration: Neo4jAdministrationRepository
    experiments: Neo4jExperimentIndexRepository
    findings: Neo4jFindingRepository

    @classmethod
    def connect(cls, driver: object, database: str) -> Self:
        from .neo4j_administration_repository import Neo4jAdministrationRepository
        from .neo4j_evidence_repository import Neo4jEvidenceRepository

        status = manager(driver, database).validate()
        if not status.is_current:
            detail = "; ".join(status.issues) or "schema version is not current"
            raise RepositorySchemaError(f"repository schema validation failed: {detail}")
        return cls(
            captures=Neo4jCaptureRepository(driver, database),
            packets=Neo4jPacketRepository(driver, database),
            enrichment=Neo4jEnrichmentRepository(driver, database),
            profiles=Neo4jProfileRepository(driver, database),
            inspection=Neo4jInspectionRepository(driver, database),
            evidence=Neo4jEvidenceRepository(driver, database),
            administration=Neo4jAdministrationRepository(driver, database),
            experiments=Neo4jExperimentIndexRepository(driver, database),
            findings=Neo4jFindingRepository(driver, database),
        )
