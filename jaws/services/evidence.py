"""Plan and apply portable evidence transfer over an exact repository port."""

from __future__ import annotations

from dataclasses import dataclass

from jaws.domain import (
    Clock,
    EvidenceBundle,
    EvidenceImportPlan,
    EvidenceImportResult,
)
from jaws.ports import (
    EvidenceImportConflictError,
    EvidenceRepository,
    EvidenceSchemaConflictError,
)


@dataclass(frozen=True, slots=True)
class EvidenceTransferService:
    repository: EvidenceRepository
    clock: Clock

    def export(self) -> EvidenceBundle:
        evidence = self.repository.snapshot()
        return EvidenceBundle.build(evidence, exported_at=self.clock.now())

    def plan_import(self, bundle: EvidenceBundle) -> EvidenceImportPlan:
        target_schema = self.repository.schema_provenance()
        if target_schema != bundle.evidence.schema:
            raise EvidenceSchemaConflictError(
                "evidence bundle schema provenance does not match the import target"
            )
        return EvidenceImportPlan(
            schema=target_schema,
            record_counts=bundle.evidence.record_counts,
            content_checksum=bundle.evidence.content_checksum,
            target_empty=self.repository.is_empty(),
        )

    def apply_import(
        self, bundle: EvidenceBundle, plan: EvidenceImportPlan
    ) -> EvidenceImportResult:
        current = self.plan_import(bundle)
        if current != plan:
            raise EvidenceImportConflictError("evidence import target changed after planning")
        if not plan.target_empty:
            raise EvidenceImportConflictError("evidence import target is not empty")
        self.repository.restore(bundle.evidence)
        restored = self.repository.snapshot()
        if restored.content_checksum != bundle.evidence.content_checksum:
            raise EvidenceImportConflictError(
                "restored evidence checksum does not match the reviewed bundle"
            )
        return EvidenceImportResult(plan=plan, applied=True)

    def dry_run_import(self, bundle: EvidenceBundle) -> EvidenceImportResult:
        return EvidenceImportResult(plan=self.plan_import(bundle), applied=False)
