"""Ordered Neo4j schema migrations and administrative operations."""

from jaws.domain import Clock

from .manager import Neo4jMigrationManager
from .models import (
    AppliedMigration,
    CypherStatement,
    Migration,
    MigrationError,
    MigrationPlan,
    MigrationResult,
    SchemaObject,
    SchemaStatus,
)
from .v0001_adopt_legacy_schema import MIGRATION as V0001
from .v0002_capture_identity_and_scope import MIGRATION as V0002

MIGRATIONS = (V0001, V0002)


def manager(driver: object, database: str, *, clock: Clock | None = None) -> Neo4jMigrationManager:
    """Build the canonical manager without making driver creation an import side effect."""

    return Neo4jMigrationManager(driver, database, MIGRATIONS, clock=clock)


__all__ = [
    "MIGRATIONS",
    "V0001",
    "V0002",
    "AppliedMigration",
    "CypherStatement",
    "Migration",
    "MigrationError",
    "MigrationPlan",
    "MigrationResult",
    "Neo4jMigrationManager",
    "SchemaObject",
    "SchemaStatus",
    "manager",
]
