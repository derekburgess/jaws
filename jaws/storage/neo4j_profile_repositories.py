"""Neo4j enrichment and versioned endpoint-profile repositories."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping, Sequence
from datetime import datetime
from typing import Any, Protocol, Self, TypeVar, cast

from jaws.domain import (
    AuditEvent,
    EndpointProfile,
    EnrichmentRecord,
    EnrichmentStatus,
    EntityId,
    EntityMetadata,
    ObservationScopeId,
    OutlierStatus,
    ProfileIdentity,
    ProfileScopeSummary,
    ProfileStatus,
    ResearcherAnnotation,
    canonical_digest,
    utc_text,
)
from jaws.ports import EntityNotFoundError, ProfileScopeConflictError, RetentionConflictError

from .audit import CREATE_AUDIT_EVENT_QUERY, audit_parameters

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


_GET_ENRICHMENT = """
MATCH (address:IP_ADDRESS {IP_ADDRESS: $ip_address})
RETURN address.ENRICHMENT_STATUS AS status,
       toString(address.ENRICHED_AT) AS acquired_at,
       address.ENRICHMENT_PROVIDER_ID AS provider_id,
       address.ENRICHMENT_PROVIDER_REVISION AS provider_revision,
       address.ENRICHMENT_ORGANIZATION AS organization,
       address.ENRICHMENT_ASN AS asn,
       address.HOSTNAME AS hostname,
       address.LOCATION AS location,
       address.COORDINATES AS coordinates,
       address.ENRICHMENT_CONFIDENCE AS confidence,
       address.ENRICHMENT_FAILURE_CODE AS failure_code
"""

_PUT_ENRICHMENT = """
MATCH (address:IP_ADDRESS {IP_ADDRESS: $ip_address})
OPTIONAL MATCH (address)<-[previous_ownership:OWNERSHIP]-(:ORGANIZATION)
DELETE previous_ownership
WITH DISTINCT address
SET address.ENRICHMENT_STATUS = $status,
    address.ENRICHED_AT = datetime($acquired_at),
    address.ENRICHMENT_PROVIDER_ID = $provider_id,
    address.ENRICHMENT_PROVIDER_REVISION = $provider_revision,
    address.ENRICHMENT_ORGANIZATION = $organization,
    address.ENRICHMENT_ASN = $asn,
    address.HOSTNAME = $hostname,
    address.LOCATION = $location,
    address.COORDINATES = $coordinates,
    address.ENRICHMENT_CONFIDENCE = $confidence,
    address.ENRICHMENT_FAILURE_CODE = $failure_code
FOREACH (_ IN CASE WHEN $organization IS NULL THEN [] ELSE [1] END |
    MERGE (organization:ORGANIZATION {ORGANIZATION: $organization})
    MERGE (organization)-[:OWNERSHIP]->(address)
)
RETURN address.IP_ADDRESS AS ip_address
"""

_PROFILE_FIELDS = """
endpoint.PROFILE_KEY AS profile_key,
endpoint.SCOPE_ID AS scope_id,
endpoint.CAPTURE_ID AS legacy_scope,
endpoint.IP_ADDRESS AS ip_address,
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
"""

_READ_SCOPE = f"""
MATCH (endpoint:ENDPOINT {{SCOPE_ID: $scope_id}})
RETURN {_PROFILE_FIELDS}
ORDER BY endpoint.IP_ADDRESS, endpoint.PROFILE_KEY
"""

_READ_HISTORY = f"""
MATCH (target_scope:OBSERVATION_SCOPE {{SCOPE_ID: $scope_id}})-[:INCLUDES]->
      (target_capture:CAPTURE)
WITH coalesce(target_capture.STARTED_AT, target_capture.STARTED) AS target_started
MATCH (historical_scope:OBSERVATION_SCOPE)-[:INCLUDES]->(historical_capture:CAPTURE)
MATCH (endpoint:ENDPOINT {{SCOPE_ID: historical_scope.SCOPE_ID}})
WHERE historical_scope.SCOPE_ID <> $scope_id
  AND historical_scope.SCOPE_ID <> 'scope_pooled_all'
  AND coalesce(historical_capture.STARTED_AT, historical_capture.STARTED) < target_started
RETURN {_PROFILE_FIELDS},
       toString(coalesce(historical_capture.STARTED_AT,
                         historical_capture.STARTED)) AS evidence_started
ORDER BY evidence_started, endpoint.SCOPE_ID, endpoint.IP_ADDRESS,
         endpoint.PROFILE_KEY
"""

_CREATE_PROFILES = """
UNWIND $profiles AS profile
MATCH (address:IP_ADDRESS {IP_ADDRESS: profile.ip_address})
CREATE (address)-[:PROFILE]->(endpoint:ENDPOINT {
    PROFILE_KEY: profile.profile_key,
    SCOPE_ID: $scope_id,
    CAPTURE_ID: profile.legacy_scope,
    IP_ADDRESS: profile.ip_address,
    REPRESENTATION_ID: profile.representation_id,
    REPRESENTATION_VERSION: profile.representation_version,
    MODEL_ID: profile.model_id,
    MODEL_REVISION: profile.model_revision,
    TIMESTAMP: datetime(profile.computed_at),
    ENDPOINT_TYPE: profile.address_classification,
    ORGANIZATION: profile.organization,
    HOSTNAME: profile.hostname,
    LOCATION: profile.location,
    BYTES_OUT: profile.bytes_out,
    PACKETS_OUT: profile.packets_out,
    OUT_PEERS: profile.out_peers,
    OUT_PORTS: profile.out_ports,
    BYTES_IN: profile.bytes_in,
    PACKETS_IN: profile.packets_in,
    IN_PEERS: profile.in_peers,
    IN_PORTS: profile.in_ports,
    PROTOCOLS: profile.protocols,
    INTERVAL_MEAN: profile.interval_mean,
    INTERVAL_CV: profile.interval_cv,
    EMBEDDING: profile.embedding,
    OUTLIER_STATUS: profile.outlier_status,
    OUTLIER: profile.legacy_outlier,
    PROFILE_STATUS: profile.profile_status
})
RETURN count(endpoint) AS created
"""


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ProfileScopeConflictError(f"stored record is missing {name}")
    return value


def _optional_text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _datetime(value: object) -> datetime | None:
    text = _optional_text(value)
    return datetime.fromisoformat(text.replace("Z", "+00:00")) if text else None


def _required_datetime(value: object, name: str) -> datetime:
    parsed = _datetime(value)
    if parsed is None:
        raise ProfileScopeConflictError(f"stored record is missing {name}")
    return parsed


def _outlier(status: object, legacy: object) -> OutlierStatus:
    if isinstance(status, str):
        return OutlierStatus(status)
    if legacy is True:
        return OutlierStatus.OUTLIER
    if legacy is False:
        return OutlierStatus.INLIER
    return OutlierStatus.NOT_SCORED


def _profile_status(value: object) -> ProfileStatus:
    return ProfileStatus(value) if isinstance(value, str) else ProfileStatus.LEGACY_UNVERSIONED


def _profile(row: _Record) -> EndpointProfile:
    scope_id = ObservationScopeId(_text(row["scope_id"], "SCOPE_ID"))
    address = _text(row["ip_address"], "IP_ADDRESS")
    status = _profile_status(row["profile_status"])
    representation_id = _optional_text(row["representation_id"]) or "legacy_endpoint_profile"
    representation_version = _optional_text(row["representation_version"]) or "unversioned"
    model_id = _optional_text(row["model_id"])
    model_revision = _optional_text(row["model_revision"])
    if (model_id is None) != (model_revision is None):
        model_id = None
        model_revision = None
    return EndpointProfile(
        identity=ProfileIdentity(
            entity_id=EntityId(f"ip:{address}"),
            scope_id=scope_id,
            representation_id=representation_id,
            representation_version=representation_version,
            model_id=model_id,
            model_revision=model_revision,
        ),
        legacy_scope=_optional_text(row["legacy_scope"]) or "legacy_unstamped",
        computed_at=_datetime(row["computed_at"]),
        address_classification=_optional_text(row["address_classification"]) or "unknown",
        organization=_optional_text(row["organization"]),
        hostname=_optional_text(row["hostname"]),
        location=_optional_text(row["location"]),
        bytes_out=int(row["bytes_out"] or 0),
        packets_out=int(row["packets_out"] or 0),
        out_peers=int(row["out_peers"] or 0),
        out_ports=tuple(row["out_ports"] or ()),
        bytes_in=int(row["bytes_in"] or 0),
        packets_in=int(row["packets_in"] or 0),
        in_peers=int(row["in_peers"] or 0),
        in_ports=tuple(row["in_ports"] or ()),
        protocols=tuple(row["protocols"] or ()),
        interval_mean=float(row["interval_mean"]) if row["interval_mean"] is not None else None,
        interval_cv=float(row["interval_cv"]) if row["interval_cv"] is not None else None,
        embedding=tuple(float(value) for value in (row["embedding"] or ())),
        outlier=_outlier(row["outlier_status"], row["legacy_outlier"]),
        status=status,
    )


class Neo4jEnrichmentRepository:
    def __init__(self, driver: object, database: str) -> None:
        if not database.strip():
            raise ValueError("database name cannot be empty")
        self._driver = cast(_Driver, driver)
        self.database = database

    def count_entities(self) -> int:
        with self._driver.session(database=self.database) as session:
            row = session.run("MATCH (address:IP_ADDRESS) RETURN count(address) AS total").single()
        return int(row["total"]) if row else 0

    def pending_addresses(self) -> tuple[str, ...]:
        query = """
        MATCH (address:IP_ADDRESS)
        WHERE (
            address.ENRICHMENT_STATUS IS NULL
            AND NOT (address)<-[:OWNERSHIP]-(:ORGANIZATION)
        ) OR address.ENRICHMENT_STATUS = 'transient_failure'
        RETURN address.IP_ADDRESS AS ip_address
        ORDER BY ip_address
        """
        with self._driver.session(database=self.database) as session:
            return tuple(str(row["ip_address"]) for row in session.run(query))

    def get(self, entity_id: EntityId) -> EnrichmentRecord | None:
        address = entity_id.value.removeprefix("ip:")
        with self._driver.session(database=self.database) as session:
            row = session.run(_GET_ENRICHMENT, {"ip_address": address}).single()
        if row is None or row["status"] is None:
            return None
        return EnrichmentRecord(
            entity_id=entity_id,
            ip_address=address,
            status=EnrichmentStatus(_text(row["status"], "ENRICHMENT_STATUS")),
            acquired_at=_required_datetime(row["acquired_at"], "ENRICHED_AT"),
            provider_id=_text(row["provider_id"], "ENRICHMENT_PROVIDER_ID"),
            provider_revision=_text(row["provider_revision"], "ENRICHMENT_PROVIDER_REVISION"),
            organization=_optional_text(row["organization"]),
            asn=_optional_text(row["asn"]),
            hostname=_optional_text(row["hostname"]),
            location=_optional_text(row["location"]),
            coordinates=_optional_text(row["coordinates"]),
            confidence=float(row["confidence"]) if row["confidence"] is not None else None,
            failure_code=_optional_text(row["failure_code"]),
        )

    def list_metadata(self) -> tuple[EntityMetadata, ...]:
        query = """
        MATCH (address:IP_ADDRESS)
        OPTIONAL MATCH (organization:ORGANIZATION)-[:OWNERSHIP]->(address)
        WITH address, organization
        ORDER BY organization.ORGANIZATION
        RETURN address.IP_ADDRESS AS ip_address,
               head(collect(organization.ORGANIZATION)) AS organization,
               address.HOSTNAME AS hostname,
               address.LOCATION AS location,
               address.COORDINATES AS coordinates
        ORDER BY ip_address
        """
        with self._driver.session(database=self.database) as session:
            return tuple(
                EntityMetadata(
                    entity_id=EntityId(f"ip:{_text(row['ip_address'], 'IP_ADDRESS')}"),
                    ip_address=_text(row["ip_address"], "IP_ADDRESS"),
                    organization=_optional_text(row["organization"]),
                    hostname=_optional_text(row["hostname"]),
                    location=_optional_text(row["location"]),
                    coordinates=_optional_text(row["coordinates"]),
                )
                for row in session.run(query)
            )

    def put(self, record: EnrichmentRecord) -> None:
        parameters: dict[str, object] = {
            "ip_address": record.ip_address,
            "status": record.status.value,
            "acquired_at": utc_text(record.acquired_at),
            "provider_id": record.provider_id,
            "provider_revision": record.provider_revision,
            "organization": record.organization,
            "asn": record.asn,
            "hostname": record.hostname,
            "location": record.location,
            "coordinates": record.coordinates,
            "confidence": record.confidence,
            "failure_code": record.failure_code,
        }
        with self._driver.session(database=self.database) as session:
            row = session.execute_write(
                lambda transaction: transaction.run(_PUT_ENRICHMENT, parameters).single()
            )
        if row is None:
            raise EntityNotFoundError(f"entity does not exist: {record.entity_id}")

    def add_annotation(self, annotation: ResearcherAnnotation) -> None:
        annotation_key = "annotation_" + str(
            canonical_digest({"entity_id": annotation.entity_id, "key": annotation.key})
        )
        query = """
        MATCH (address:IP_ADDRESS {IP_ADDRESS: $ip_address})
        MERGE (annotation:ENTITY_ANNOTATION {ANNOTATION_KEY: $annotation_key})
        SET annotation.ENTITY_ID = $entity_id,
            annotation.KEY = $key,
            annotation.VALUE = $value,
            annotation.AUTHOR = $author,
            annotation.RECORDED_AT = datetime($recorded_at),
            annotation.GROUND_TRUTH = $ground_truth
        MERGE (address)-[:ANNOTATED_WITH]->(annotation)
        RETURN address.IP_ADDRESS AS ip_address
        """
        parameters: dict[str, object] = {
            "ip_address": annotation.entity_id.value.removeprefix("ip:"),
            "annotation_key": annotation_key,
            "entity_id": annotation.entity_id.value,
            "key": annotation.key,
            "value": annotation.value,
            "author": annotation.author,
            "recorded_at": utc_text(annotation.recorded_at),
            "ground_truth": annotation.ground_truth,
        }
        with self._driver.session(database=self.database) as session:
            row = session.execute_write(
                lambda transaction: transaction.run(query, parameters).single()
            )
        if row is None:
            raise EntityNotFoundError(f"entity does not exist: {annotation.entity_id}")

    def annotations(self, entity_id: EntityId) -> tuple[ResearcherAnnotation, ...]:
        query = """
        MATCH (:IP_ADDRESS {IP_ADDRESS: $ip_address})-[:ANNOTATED_WITH]->(annotation)
        RETURN annotation.KEY AS key, annotation.VALUE AS value,
               annotation.AUTHOR AS author, toString(annotation.RECORDED_AT) AS recorded_at,
               annotation.GROUND_TRUTH AS ground_truth
        ORDER BY annotation.RECORDED_AT, annotation.KEY
        """
        with self._driver.session(database=self.database) as session:
            return tuple(
                ResearcherAnnotation(
                    entity_id=entity_id,
                    key=_text(row["key"], "KEY"),
                    value=_text(row["value"], "VALUE"),
                    author=_text(row["author"], "AUTHOR"),
                    recorded_at=_required_datetime(row["recorded_at"], "RECORDED_AT"),
                    ground_truth=bool(row["ground_truth"]),
                )
                for row in session.run(query, {"ip_address": entity_id.value.removeprefix("ip:")})
            )

    def legacy_unknown_addresses(self) -> tuple[str, ...]:
        query = """
        MATCH (:ORGANIZATION {ORGANIZATION: 'Unknown'})-[:OWNERSHIP]->(address:IP_ADDRESS)
        RETURN address.IP_ADDRESS AS ip_address
        ORDER BY ip_address
        """
        with self._driver.session(database=self.database) as session:
            return tuple(str(row["ip_address"]) for row in session.run(query))

    def remove_legacy_unknown_ownership(self, addresses: Sequence[str]) -> int:
        unique = tuple(sorted(set(addresses)))
        if not unique:
            return 0
        query = """
        MATCH (organization:ORGANIZATION {ORGANIZATION: 'Unknown'})
              -[ownership:OWNERSHIP]->(address:IP_ADDRESS)
        WHERE address.IP_ADDRESS IN $addresses
        DELETE ownership
        WITH DISTINCT organization, count(ownership) AS removed
        FOREACH (_ IN CASE WHEN NOT (organization)-[:OWNERSHIP]->() THEN [1] ELSE [] END |
            DELETE organization
        )
        RETURN removed
        """
        with self._driver.session(database=self.database) as session:
            row = session.execute_write(
                lambda transaction: transaction.run(query, {"addresses": list(unique)}).single()
            )
        return int(row["removed"]) if row else 0


class Neo4jProfileRepository:
    def __init__(self, driver: object, database: str) -> None:
        if not database.strip():
            raise ValueError("database name cannot be empty")
        self._driver = cast(_Driver, driver)
        self.database = database

    def replace_scope(
        self, scope_id: ObservationScopeId, records: Sequence[EndpointProfile]
    ) -> int:
        batch = tuple(records)
        if any(record.identity.scope_id != scope_id for record in batch):
            raise ProfileScopeConflictError("every profile must belong to the replaced scope")
        if len({record.profile_key for record in batch}) != len(batch):
            raise ProfileScopeConflictError("profile keys must be unique within a scope")
        if any(
            record.computed_at is None or record.status is not ProfileStatus.CURRENT
            for record in batch
        ):
            raise ProfileScopeConflictError(
                "new profiles require computed_at and current provenance status"
            )
        profiles: list[dict[str, object]] = []
        for record in batch:
            profiles.append(
                {
                    "profile_key": record.profile_key,
                    "legacy_scope": record.legacy_scope,
                    "ip_address": record.identity.entity_id.value.removeprefix("ip:"),
                    "representation_id": record.identity.representation_id,
                    "representation_version": record.identity.representation_version,
                    "model_id": record.identity.model_id,
                    "model_revision": record.identity.model_revision,
                    "computed_at": utc_text(cast(datetime, record.computed_at)),
                    "address_classification": record.address_classification,
                    "organization": record.organization,
                    "hostname": record.hostname,
                    "location": record.location,
                    "bytes_out": record.bytes_out,
                    "packets_out": record.packets_out,
                    "out_peers": record.out_peers,
                    "out_ports": list(record.out_ports),
                    "bytes_in": record.bytes_in,
                    "packets_in": record.packets_in,
                    "in_peers": record.in_peers,
                    "in_ports": list(record.in_ports),
                    "protocols": list(record.protocols),
                    "interval_mean": record.interval_mean,
                    "interval_cv": record.interval_cv,
                    "embedding": list(record.embedding),
                    "outlier_status": record.outlier.value,
                    "legacy_outlier": (
                        True
                        if record.outlier is OutlierStatus.OUTLIER
                        else False
                        if record.outlier is OutlierStatus.INLIER
                        else None
                    ),
                    "profile_status": record.status.value,
                }
            )

        def write(transaction: _Transaction) -> int:
            scope = transaction.run(
                "MATCH (scope:OBSERVATION_SCOPE {SCOPE_ID: $scope_id}) "
                "RETURN scope.SCOPE_ID AS scope_id",
                {"scope_id": scope_id.value},
            ).single()
            if scope is None:
                raise ProfileScopeConflictError(f"observation scope does not exist: {scope_id}")
            if profiles:
                addresses = [profile["ip_address"] for profile in profiles]
                row = transaction.run(
                    "MATCH (address:IP_ADDRESS) WHERE address.IP_ADDRESS IN $addresses "
                    "RETURN count(DISTINCT address) AS count",
                    {"addresses": addresses},
                ).single()
                if row is None or int(row["count"]) != len(set(addresses)):
                    raise EntityNotFoundError("one or more profile entities do not exist")
            transaction.run(
                "MATCH (endpoint:ENDPOINT {SCOPE_ID: $scope_id}) DETACH DELETE endpoint",
                {"scope_id": scope_id.value},
            ).consume()
            if not profiles:
                return 0
            row = transaction.run(
                _CREATE_PROFILES,
                {"scope_id": scope_id.value, "profiles": profiles},
            ).single()
            return int(row["created"]) if row else 0

        with self._driver.session(database=self.database) as session:
            return session.execute_write(write)

    def read_scope(self, scope_id: ObservationScopeId) -> tuple[EndpointProfile, ...]:
        with self._driver.session(database=self.database) as session:
            return tuple(
                _profile(row) for row in session.run(_READ_SCOPE, {"scope_id": scope_id.value})
            )

    def read_history(self, scope_id: ObservationScopeId) -> tuple[EndpointProfile, ...]:
        if scope_id.value == "scope_pooled_all":
            return ()
        with self._driver.session(database=self.database) as session:
            return tuple(
                _profile(row) for row in session.run(_READ_HISTORY, {"scope_id": scope_id.value})
            )

    def list_scopes(self) -> tuple[ProfileScopeSummary, ...]:
        query = """
        MATCH (endpoint:ENDPOINT)
        WHERE endpoint.SCOPE_ID IS NOT NULL
        RETURN endpoint.SCOPE_ID AS scope_id,
               head(collect(endpoint.CAPTURE_ID)) AS legacy_scope,
               toString(max(endpoint.TIMESTAMP)) AS computed_at,
               count(endpoint) AS profile_count,
               head(collect(endpoint.PROFILE_STATUS)) AS profile_status
        ORDER BY computed_at DESC, scope_id
        """
        with self._driver.session(database=self.database) as session:
            return tuple(
                ProfileScopeSummary(
                    scope_id=ObservationScopeId(_text(row["scope_id"], "SCOPE_ID")),
                    legacy_scope=_optional_text(row["legacy_scope"]) or "legacy_unstamped",
                    computed_at=_datetime(row["computed_at"]),
                    profile_count=int(row["profile_count"]),
                    status=_profile_status(row["profile_status"]),
                )
                for row in session.run(query)
            )

    def set_outliers(
        self, scope_id: ObservationScopeId, verdicts: Mapping[EntityId, OutlierStatus]
    ) -> int:
        values = [
            {
                "ip_address": entity.value.removeprefix("ip:"),
                "status": status.value,
                "legacy": (
                    True
                    if status is OutlierStatus.OUTLIER
                    else False
                    if status is OutlierStatus.INLIER
                    else None
                ),
            }
            for entity, status in verdicts.items()
        ]
        if not values:
            return 0
        validate_query = """
        UNWIND $verdicts AS verdict
        MATCH (endpoint:ENDPOINT {SCOPE_ID: $scope_id, IP_ADDRESS: verdict.ip_address})
        RETURN count(DISTINCT endpoint.IP_ADDRESS) AS matched
        """
        update_query = """
        UNWIND $verdicts AS verdict
        MATCH (endpoint:ENDPOINT {SCOPE_ID: $scope_id, IP_ADDRESS: verdict.ip_address})
        SET endpoint.OUTLIER_STATUS = verdict.status,
            endpoint.OUTLIER = verdict.legacy
        RETURN count(endpoint) AS updated
        """

        def write(transaction: _Transaction) -> int:
            parameters: dict[str, object] = {
                "scope_id": scope_id.value,
                "verdicts": values,
            }
            matched = transaction.run(validate_query, parameters).single()
            if matched is None or int(matched["matched"]) != len(values):
                raise EntityNotFoundError("one or more profiles do not exist in the scope")
            updated = transaction.run(update_query, parameters).single()
            return int(updated["updated"]) if updated else 0

        with self._driver.session(database=self.database) as session:
            return session.execute_write(write)

    def delete_scopes(
        self,
        expected: Sequence[ProfileScopeSummary],
        *,
        audit_event: AuditEvent | None = None,
    ) -> int:
        requested = tuple(expected)
        if audit_event is not None and audit_event.context.target_database != self.database:
            raise RetentionConflictError("retention audit targets a different database")
        if not requested and audit_event is None:
            return 0
        if len({summary.scope_id for summary in requested}) != len(requested):
            raise ValueError("retention scope identities must be unique")
        values = [
            {
                "scope_id": summary.scope_id.value,
                "legacy_scope": summary.legacy_scope,
                "computed_at": utc_text(summary.computed_at) if summary.computed_at else None,
                "profile_count": summary.profile_count,
                "status": summary.status.value,
            }
            for summary in requested
        ]
        validate_query = """
        UNWIND $scopes AS expected
        OPTIONAL MATCH (endpoint:ENDPOINT {SCOPE_ID: expected.scope_id})
        RETURN expected.scope_id AS scope_id,
               head(collect(endpoint.CAPTURE_ID)) AS legacy_scope,
               toString(max(endpoint.TIMESTAMP)) AS computed_at,
               count(endpoint) AS profile_count,
               head(collect(endpoint.PROFILE_STATUS)) AS profile_status
        ORDER BY scope_id
        """
        delete_query = """
        MATCH (endpoint:ENDPOINT)
        WHERE endpoint.SCOPE_ID IN $scope_ids
        DETACH DELETE endpoint
        RETURN count(endpoint) AS deleted
        """

        def write(transaction: _Transaction) -> int:
            if not requested:
                assert audit_event is not None
                transaction.run(CREATE_AUDIT_EVENT_QUERY, audit_parameters(audit_event)).consume()
                return 0
            rows = transaction.run(validate_query, {"scopes": values})
            current = tuple(
                sorted(
                    (
                        ProfileScopeSummary(
                            scope_id=ObservationScopeId(_text(row["scope_id"], "SCOPE_ID")),
                            legacy_scope=_optional_text(row["legacy_scope"]) or "legacy_unstamped",
                            computed_at=_datetime(row["computed_at"]),
                            profile_count=int(row["profile_count"]),
                            status=_profile_status(row["profile_status"]),
                        )
                        for row in rows
                    ),
                    key=lambda summary: summary.scope_id.value,
                )
            )
            if current != tuple(sorted(requested, key=lambda summary: summary.scope_id.value)):
                raise RetentionConflictError("profile scopes changed after retention was planned")
            deleted = transaction.run(
                delete_query,
                {"scope_ids": [summary.scope_id.value for summary in requested]},
            ).single()
            if audit_event is not None:
                transaction.run(CREATE_AUDIT_EVENT_QUERY, audit_parameters(audit_event)).consume()
            return int(deleted["deleted"]) if deleted else 0

        with self._driver.session(database=self.database) as session:
            return session.execute_write(write)
