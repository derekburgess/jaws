"""Immutable records for ordered graph-schema migrations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from jaws.domain import canonical_digest

SchemaObjectKind = Literal["constraint", "index"]


@dataclass(frozen=True, slots=True, order=True)
class SchemaObject:
    """A named Neo4j schema object required by a migration."""

    kind: SchemaObjectKind
    name: str
    label: str
    properties: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.name or not self.label or not self.properties:
            raise ValueError("schema objects require a name, label, and properties")


@dataclass(frozen=True, slots=True)
class CypherStatement:
    """One idempotent migration statement and its human-readable purpose."""

    description: str
    query: str

    def __post_init__(self) -> None:
        if not self.description or not self.query.strip():
            raise ValueError("migration statements require a description and query")


@dataclass(frozen=True, slots=True)
class Migration:
    """An immutable, ordered schema transition."""

    version: int
    name: str
    statements: tuple[CypherStatement, ...]
    required_schema: tuple[SchemaObject, ...]
    reversible: bool
    rollback: str

    def __post_init__(self) -> None:
        if self.version < 1:
            raise ValueError("migration version must be positive")
        if not self.name or not self.statements or not self.rollback:
            raise ValueError("migration name, statements, and rollback policy are required")

    @property
    def checksum(self) -> str:
        """Digest migration meaning so an applied version cannot be edited in place."""

        return str(
            canonical_digest(
                {
                    "version": self.version,
                    "name": self.name,
                    "statements": self.statements,
                    "required_schema": self.required_schema,
                    "reversible": self.reversible,
                    "rollback": self.rollback,
                }
            )
        )


@dataclass(frozen=True, slots=True)
class AppliedMigration:
    version: int
    name: str
    checksum: str
    applied_at: str


@dataclass(frozen=True, slots=True)
class MigrationPlan:
    current_version: int | None
    target_version: int
    pending: tuple[Migration, ...]
    statements: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SchemaStatus:
    current_version: int | None
    target_version: int
    applied: tuple[AppliedMigration, ...]
    pending_versions: tuple[int, ...]
    issues: tuple[str, ...]

    @property
    def is_current(self) -> bool:
        return not self.pending_versions and not self.issues


@dataclass(frozen=True, slots=True)
class MigrationResult:
    applied_versions: tuple[int, ...]
    status: SchemaStatus


class MigrationError(RuntimeError):
    """Raised when migration history or live schema cannot be advanced safely."""
