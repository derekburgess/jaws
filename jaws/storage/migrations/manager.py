"""Neo4j migration planning, validation, and execution."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from datetime import UTC, datetime
from typing import Any, Protocol, TypeVar, cast

from jaws.domain import Clock, utc_text

from .models import (
    AppliedMigration,
    Migration,
    MigrationError,
    MigrationPlan,
    MigrationResult,
    SchemaObject,
    SchemaObjectKind,
    SchemaStatus,
)

ResultT = TypeVar("ResultT")


class _Record(Protocol):
    def __getitem__(self, key: str) -> Any: ...


class _Result(Protocol):
    def __iter__(self) -> Iterator[_Record]: ...

    def consume(self) -> Any: ...


class _Transaction(Protocol):
    def run(self, query: str, parameters: Mapping[str, object] | None = None) -> _Result: ...


class _Session(Protocol):
    def __enter__(self) -> _Session: ...

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None: ...

    def run(self, query: str, parameters: Mapping[str, object] | None = None) -> _Result: ...

    def execute_write(self, work: Callable[[_Transaction], ResultT]) -> ResultT: ...


class _Driver(Protocol):
    def session(self, *, database: str) -> _Session: ...


class _SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


_APPLIED_QUERY = """
MATCH (migration:JAWS_SCHEMA_MIGRATION)
RETURN migration.VERSION AS version,
       migration.NAME AS name,
       migration.CHECKSUM AS checksum,
       toString(migration.APPLIED_AT) AS applied_at
ORDER BY version
"""

_CONSTRAINTS_QUERY = """
SHOW CONSTRAINTS YIELD name, labelsOrTypes, properties
RETURN name, labelsOrTypes, properties
"""

_INDEXES_QUERY = """
SHOW INDEXES YIELD name, labelsOrTypes, properties, owningConstraint
WHERE owningConstraint IS NULL
RETURN name, labelsOrTypes, properties
"""

_RECORD_QUERY = """
CREATE (:JAWS_SCHEMA_MIGRATION {
    VERSION: $version,
    NAME: $name,
    CHECKSUM: $checksum,
    APPLIED_AT: datetime($applied_at)
})
"""


class Neo4jMigrationManager:
    """Advance one Neo4j database through an immutable migration registry."""

    def __init__(
        self,
        driver: object,
        database: str,
        migrations: tuple[Migration, ...],
        *,
        clock: Clock | None = None,
    ) -> None:
        if not database.strip():
            raise ValueError("database name cannot be empty")
        versions = tuple(migration.version for migration in migrations)
        if versions != tuple(range(1, len(migrations) + 1)):
            raise ValueError("migration versions must be contiguous, ordered, and start at 1")
        if len({migration.name for migration in migrations}) != len(migrations):
            raise ValueError("migration names must be unique")
        self._driver = cast(_Driver, driver)
        self.database = database
        self.migrations = migrations
        self._clock = clock or _SystemClock()

    @property
    def target_version(self) -> int:
        return self.migrations[-1].version if self.migrations else 0

    def status(self) -> SchemaStatus:
        with self._driver.session(database=self.database) as session:
            applied = self._read_applied(session)
            observed = self._read_schema(session)
        return self._build_status(applied, observed)

    def validate(self) -> SchemaStatus:
        """Return complete validation details without modifying the database."""

        return self.status()

    def dry_run(self) -> MigrationPlan:
        """Plan pending statements without executing or recording a migration."""

        status = self.status()
        blocking = tuple(
            issue for issue in status.issues if not issue.startswith("pending migration")
        )
        if blocking:
            raise MigrationError("schema cannot be migrated safely: " + "; ".join(blocking))
        pending = tuple(
            migration
            for migration in self.migrations
            if migration.version in status.pending_versions
        )
        return MigrationPlan(
            current_version=status.current_version,
            target_version=self.target_version,
            pending=pending,
            statements=tuple(
                statement.query for migration in pending for statement in migration.statements
            ),
        )

    def migrate(self) -> MigrationResult:
        """Apply pending idempotent statements, validate, then record each version."""

        plan = self.dry_run()
        applied_versions: list[int] = []
        for migration in plan.pending:
            with self._driver.session(database=self.database) as session:
                for statement in migration.statements:
                    session.run(statement.query).consume()
                session.run("CALL db.awaitIndexes(300)").consume()
                observed = self._read_schema(session)
                missing = self._missing_schema(migration.required_schema, observed)
                if missing:
                    names = ", ".join(item.name for item in missing)
                    raise MigrationError(
                        f"migration {migration.version} did not establish required schema: {names}"
                    )
                parameters: Mapping[str, object] = {
                    "version": migration.version,
                    "name": migration.name,
                    "checksum": migration.checksum,
                    "applied_at": utc_text(self._clock.now()),
                }
                session.execute_write(
                    lambda transaction: transaction.run(_RECORD_QUERY, parameters).consume()
                )
            applied_versions.append(migration.version)

        status = self.status()
        if not status.is_current:
            raise MigrationError(
                "schema remains invalid after migration: " + "; ".join(status.issues)
            )
        return MigrationResult(tuple(applied_versions), status)

    def _build_status(
        self,
        applied: tuple[AppliedMigration, ...],
        observed: frozenset[SchemaObject],
    ) -> SchemaStatus:
        registered = {migration.version: migration for migration in self.migrations}
        applied_by_version: dict[int, AppliedMigration] = {}
        issues: list[str] = []
        for record in applied:
            if record.version in applied_by_version:
                issues.append(f"duplicate applied migration version {record.version}")
                continue
            applied_by_version[record.version] = record
            expected = registered.get(record.version)
            if expected is None:
                issues.append(f"unknown applied migration version {record.version}")
                continue
            if record.name != expected.name:
                issues.append(f"migration {record.version} name does not match registry")
            if record.checksum != expected.checksum:
                issues.append(f"migration {record.version} checksum does not match registry")
            missing = self._missing_schema(expected.required_schema, observed)
            issues.extend(
                f"applied migration {record.version} is missing {item.kind} {item.name}"
                for item in missing
            )

        known_versions = tuple(sorted(set(applied_by_version) & set(registered)))
        if known_versions and known_versions != tuple(range(1, max(known_versions) + 1)):
            issues.append("applied migration history is not contiguous from version 1")

        pending = tuple(
            migration.version
            for migration in self.migrations
            if migration.version not in applied_by_version
        )
        issues.extend(f"pending migration {version}" for version in pending)
        current = max(applied_by_version, default=None)
        return SchemaStatus(current, self.target_version, applied, pending, tuple(issues))

    @staticmethod
    def _missing_schema(
        required: tuple[SchemaObject, ...], observed: frozenset[SchemaObject]
    ) -> tuple[SchemaObject, ...]:
        return tuple(item for item in required if item not in observed)

    @staticmethod
    def _read_applied(session: _Session) -> tuple[AppliedMigration, ...]:
        return tuple(
            AppliedMigration(
                version=int(record["version"]),
                name=str(record["name"]),
                checksum=str(record["checksum"]),
                applied_at=str(record["applied_at"]),
            )
            for record in session.run(_APPLIED_QUERY)
        )

    @staticmethod
    def _read_schema(session: _Session) -> frozenset[SchemaObject]:
        objects: set[SchemaObject] = set()
        for kind, query in (("constraint", _CONSTRAINTS_QUERY), ("index", _INDEXES_QUERY)):
            for record in session.run(query):
                labels = tuple(str(value) for value in record["labelsOrTypes"] or ())
                properties = tuple(str(value) for value in record["properties"] or ())
                if len(labels) == 1 and properties:
                    objects.add(
                        SchemaObject(
                            cast(SchemaObjectKind, kind),
                            str(record["name"]),
                            labels[0],
                            properties,
                        )
                    )
        return frozenset(objects)
