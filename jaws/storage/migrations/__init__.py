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
    MigrationSafety,
    SchemaObject,
    SchemaStatus,
    VerifiedMigrationBackup,
)
from .v0001_adopt_legacy_schema import MIGRATION as V0001
from .v0002_capture_identity_and_scope import MIGRATION as V0002
from .v0003_enrichment_and_profiles import MIGRATION as V0003
from .v0004_administration_audit import MIGRATION as V0004

MIGRATIONS = (V0001, V0002, V0003, V0004)


def manager(driver: object, database: str, *, clock: Clock | None = None) -> Neo4jMigrationManager:
    """Build the canonical manager without making driver creation an import side effect."""

    return Neo4jMigrationManager(driver, database, MIGRATIONS, clock=clock)


__all__ = [
    "MIGRATIONS",
    "V0001",
    "V0002",
    "V0003",
    "AppliedMigration",
    "CypherStatement",
    "Migration",
    "MigrationError",
    "MigrationPlan",
    "MigrationResult",
    "MigrationSafety",
    "Neo4jMigrationManager",
    "SchemaObject",
    "SchemaStatus",
    "VerifiedMigrationBackup",
    "manager",
]
