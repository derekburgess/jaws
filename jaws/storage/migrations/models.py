"""Immutable records for ordered graph-schema migrations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Literal

from jaws.domain import CanonicalDigest, canonical_digest, normalize_utc

SchemaObjectKind = Literal["constraint", "index"]


class MigrationSafety(StrEnum):
    """Evidence effect that determines whether verified backup proof is mandatory."""

    ADDITIVE = "additive"
    DESTRUCTIVE = "destructive"
    IRREVERSIBLE = "irreversible"


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
    safety: MigrationSafety = MigrationSafety.ADDITIVE

    def __post_init__(self) -> None:
        if self.version < 1:
            raise ValueError("migration version must be positive")
        if not self.name or not self.statements or not self.rollback:
            raise ValueError("migration name, statements, and rollback policy are required")

    @property
    def checksum(self) -> str:
        """Digest migration meaning so an applied version cannot be edited in place."""

        meaning = {
            "version": self.version,
            "name": self.name,
            "statements": self.statements,
            "required_schema": self.required_schema,
            "reversible": self.reversible,
            "rollback": self.rollback,
        }
        # Preserve the already-applied checksums of versions 1-4. Risk metadata was
        # introduced afterward; every future non-additive migration includes it.
        if self.safety is not MigrationSafety.ADDITIVE:
            meaning["safety"] = self.safety
        return str(canonical_digest(meaning))

    @property
    def backup_required(self) -> bool:
        # Version 1 is a non-mutating adoption of pre-existing schema objects and
        # predates the managed evidence format. Every later irreversible migration,
        # plus any explicitly destructive migration, is protected automatically.
        return self.safety is not MigrationSafety.ADDITIVE or (
            not self.reversible and self.version > 1
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
    source_schema_digest: CanonicalDigest | None = None
    backup_required_versions: tuple[int, ...] = ()

    @property
    def backup_required(self) -> bool:
        return bool(self.backup_required_versions)


@dataclass(frozen=True, slots=True)
class VerifiedMigrationBackup:
    """Proof that a checksummed bundle exactly matched the live pre-migration evidence."""

    target_database: str
    target_version: int
    protected_versions: tuple[int, ...]
    source_schema_digest: CanonicalDigest
    evidence_content_checksum: CanonicalDigest
    exported_at: datetime
    bundle_path: str

    def __post_init__(self) -> None:
        database = self.target_database.strip()
        path = self.bundle_path.strip()
        if not database or not path:
            raise ValueError("verified migration backup requires a database and bundle path")
        if self.target_version < 1 or not self.protected_versions:
            raise ValueError("verified migration backup requires protected migration versions")
        if tuple(sorted(set(self.protected_versions))) != self.protected_versions:
            raise ValueError("protected migration versions must be unique and ordered")
        for value, name in (
            (self.source_schema_digest, "source schema digest"),
            (self.evidence_content_checksum, "evidence content checksum"),
        ):
            text = str(value)
            if len(text) != 64 or any(character not in "0123456789abcdef" for character in text):
                raise ValueError(f"{name} must be lowercase SHA-256 text")
        object.__setattr__(self, "target_database", database)
        object.__setattr__(self, "bundle_path", path)
        object.__setattr__(self, "exported_at", normalize_utc(self.exported_at))


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
