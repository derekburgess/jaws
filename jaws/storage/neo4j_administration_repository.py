"""Guarded Neo4j evidence erasure that preserves schema and audit history."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from typing import Any, Protocol, Self, TypeVar, cast

from jaws.domain import (
    AdministrationPlan,
    AdministrationResourceCount,
    AuditEvent,
    EvidenceSchemaProvenance,
    SchemaMigrationProvenance,
)
from jaws.ports import AdministrationConflictError, EvidenceSchemaConflictError

from .audit import (
    CREATE_AUDIT_EVENT_QUERY,
    LIST_AUDIT_EVENTS_QUERY,
    audit_event,
    audit_parameters,
)
from .migrations import manager

ResultT = TypeVar("ResultT")


class _Record(Protocol):
    def __getitem__(self, key: str) -> Any: ...


class _Result(Protocol):
    def __iter__(self) -> Iterator[_Record]: ...

    def single(self) -> _Record | None: ...

    def consume(self) -> Any: ...


class _Transaction(Protocol):
    def run(self, query: str, parameters: Mapping[str, object] | None = None) -> _Result: ...


class _Session(_Transaction, Protocol):
    def __enter__(self) -> Self: ...

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None: ...

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

_RESOURCE_COUNTS_QUERY = """
MATCH (node)
WHERE NOT node:JAWS_SCHEMA_MIGRATION
  AND NOT node:JAWS_AUDIT_EVENT
  AND NOT (
      node:OBSERVATION_SCOPE
      AND node.SCOPE_ID IN ['scope_pooled_all', 'scope_legacy_unstamped']
  )
WITH CASE
    WHEN node:CAPTURE THEN 'captures'
    WHEN node:PACKET THEN 'packets'
    WHEN node:ENDPOINT THEN 'profiles'
    WHEN node:IP_ADDRESS THEN 'entities'
    WHEN node:OBSERVATION_SCOPE THEN 'observation_scopes'
    WHEN node:ENTITY_ANNOTATION THEN 'annotations'
    WHEN node:ORGANIZATION THEN 'organizations'
    WHEN node:PORT THEN 'ports'
    ELSE 'unclassified'
END AS resource, count(node) AS count
RETURN resource, count
ORDER BY resource
"""

_RELATIONSHIP_COUNT_QUERY = """
MATCH (source)-[relationship]->(target)
WHERE NOT (
    (source:JAWS_SCHEMA_MIGRATION OR source:JAWS_AUDIT_EVENT OR
     (source:OBSERVATION_SCOPE AND source.SCOPE_ID IN ['scope_pooled_all', 'scope_legacy_unstamped']))
    AND
    (target:JAWS_SCHEMA_MIGRATION OR target:JAWS_AUDIT_EVENT OR
     (target:OBSERVATION_SCOPE AND target.SCOPE_ID IN ['scope_pooled_all', 'scope_legacy_unstamped']))
)
RETURN count(relationship) AS count
"""

_DELETE_QUERY = """
MATCH (node)
WHERE NOT node:JAWS_SCHEMA_MIGRATION
  AND NOT node:JAWS_AUDIT_EVENT
  AND NOT (
      node:OBSERVATION_SCOPE
      AND node.SCOPE_ID IN ['scope_pooled_all', 'scope_legacy_unstamped']
  )
WITH collect(node) AS nodes, count(node) AS deleted
FOREACH (node IN nodes | DETACH DELETE node)
RETURN deleted
"""

_RESTORE_SYSTEM_SCOPES_QUERY = """
MERGE (pooled:OBSERVATION_SCOPE {SCOPE_ID: 'scope_pooled_all'})
ON CREATE SET pooled.KIND = 'pooled', pooled.CREATED_AT = datetime()
MERGE (legacy:OBSERVATION_SCOPE {SCOPE_ID: 'scope_legacy_unstamped'})
ON CREATE SET legacy.KIND = 'legacy', legacy.CREATED_AT = datetime(), legacy.QUARANTINED = true
"""


def _schema(store: _Session | _Transaction) -> EvidenceSchemaProvenance:
    migrations = tuple(
        SchemaMigrationProvenance(
            version=int(row["version"]),
            name=str(row["name"]),
            checksum=str(row["checksum"]),
        )
        for row in store.run(_SCHEMA_QUERY)
    )
    if not migrations:
        raise EvidenceSchemaConflictError("administration target has no managed schema history")
    return EvidenceSchemaProvenance(migrations[-1].version, migrations)


class Neo4jAdministrationRepository:
    def __init__(self, driver: object, database: str) -> None:
        if not database.strip():
            raise ValueError("database name cannot be empty")
        self._driver = cast(_Driver, driver)
        self.database = database.strip()

    def _plan(self, store: _Session | _Transaction) -> AdministrationPlan:
        resources = tuple(
            AdministrationResourceCount(str(row["resource"]), int(row["count"]))
            for row in store.run(_RESOURCE_COUNTS_QUERY)
        )
        relationship = store.run(_RELATIONSHIP_COUNT_QUERY).single()
        return AdministrationPlan(
            target_database=self.database,
            schema=_schema(store),
            resources=resources,
            relationship_count=int(relationship["count"]) if relationship else 0,
        )

    def plan(self) -> AdministrationPlan:
        status = manager(self._driver, self.database).validate()
        if not status.is_current:
            raise EvidenceSchemaConflictError(
                "administration schema is not current: " + "; ".join(status.issues)
            )
        with self._driver.session(database=self.database) as session:
            return self._plan(session)

    def erase(self, expected: AdministrationPlan, event: AuditEvent) -> tuple[int, int]:
        if expected.target_database != self.database:
            raise AdministrationConflictError("administration plan targets a different database")
        if event.context.target_database != self.database or event.plan_digest != expected.digest:
            raise AdministrationConflictError(
                "audit event does not describe the administration plan"
            )

        def write(transaction: _Transaction) -> tuple[int, int]:
            current = self._plan(transaction)
            if current != expected:
                raise AdministrationConflictError(
                    "managed evidence changed after administration was planned"
                )
            deleted = transaction.run(_DELETE_QUERY).single()
            deleted_nodes = int(deleted["deleted"]) if deleted else 0
            transaction.run(_RESTORE_SYSTEM_SCOPES_QUERY).consume()
            transaction.run(CREATE_AUDIT_EVENT_QUERY, audit_parameters(event)).consume()
            return deleted_nodes, expected.relationship_count

        with self._driver.session(database=self.database) as session:
            return session.execute_write(write)

    def audit_events(self, *, limit: int = 100) -> tuple[AuditEvent, ...]:
        if limit < 1:
            raise ValueError("audit event limit must be positive")
        with self._driver.session(database=self.database) as session:
            rows = session.run(LIST_AUDIT_EVENTS_QUERY, {"limit": limit})
            return tuple(audit_event(row) for row in rows)
