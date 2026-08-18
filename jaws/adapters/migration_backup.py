"""Verify a portable evidence bundle against live pre-migration evidence."""

from __future__ import annotations

from pathlib import Path

from jaws.domain import canonical_digest
from jaws.ports import EvidenceRepository
from jaws.storage.migrations import MigrationPlan, VerifiedMigrationBackup

from .evidence_bundle import load_evidence_bundle


def verify_migration_backup(
    path: Path,
    *,
    database: str,
    plan: MigrationPlan,
    evidence: EvidenceRepository,
) -> VerifiedMigrationBackup:
    """Return proof only when a valid bundle exactly matches the current evidence snapshot."""

    if not plan.backup_required:
        raise ValueError("the migration plan does not require evidence backup proof")
    if plan.source_schema_digest is None:
        raise ValueError("the migration source has no managed schema to verify")
    bundle = load_evidence_bundle(path)
    live = evidence.snapshot()
    if bundle.evidence.schema != live.schema:
        raise ValueError("evidence backup schema does not match the live migration source")
    if canonical_digest(bundle.evidence.schema) != plan.source_schema_digest:
        raise ValueError("evidence backup schema does not match the migration plan")
    if bundle.evidence.content_checksum != live.content_checksum:
        raise ValueError("evidence backup is stale or belongs to different live evidence")
    return VerifiedMigrationBackup(
        target_database=database,
        target_version=plan.target_version,
        protected_versions=plan.backup_required_versions,
        source_schema_digest=plan.source_schema_digest,
        evidence_content_checksum=bundle.evidence.content_checksum,
        exported_at=bundle.manifest.exported_at,
        bundle_path=str(path.resolve()),
    )
