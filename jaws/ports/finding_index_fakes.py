"""Deterministic in-memory finding discovery index."""

from __future__ import annotations

from dataclasses import dataclass, field

from jaws.domain import (
    CanonicalDigest,
    EntityId,
    FindingId,
    FindingIndex,
    FindingIndexBatch,
    RunId,
    RunState,
)

from .repositories import (
    DuplicateFindingError,
    ExperimentIndexRepository,
    FindingIndexConflictError,
    RunNotFoundError,
    RunStateConflictError,
)


@dataclass(slots=True)
class InMemoryFindingRepository:
    """Atomic idempotent batch publication against a finalized run artifact."""

    runs: ExperimentIndexRepository
    _findings: dict[FindingId, FindingIndex] = field(default_factory=dict)
    _published: dict[RunId, CanonicalDigest] = field(default_factory=dict)

    def publish(self, batch: FindingIndexBatch) -> int:
        run = self.runs.get(batch.run_id)
        if run is None:
            raise RunNotFoundError(f"experiment run does not exist: {batch.run_id}")
        if run.state is not RunState.SUCCEEDED:
            raise RunStateConflictError(
                f"finding index requires succeeded run; {batch.run_id} is {run.state}"
            )
        if run.artifact_digest != batch.artifact_digest:
            raise FindingIndexConflictError(
                "finding index artifact checksum does not match the finalized run"
            )
        existing_digest = self._published.get(batch.run_id)
        if existing_digest is not None:
            if (
                existing_digest == batch.digest
                and self.list_for_run(batch.run_id) == batch.findings
            ):
                return 0
            raise FindingIndexConflictError(
                "finding index was already published with other content"
            )
        duplicates = tuple(
            finding.finding_id for finding in batch.findings if finding.finding_id in self._findings
        )
        if duplicates:
            raise DuplicateFindingError(f"finding index already exists: {duplicates[0]}")
        self._findings.update((finding.finding_id, finding) for finding in batch.findings)
        self._published[batch.run_id] = batch.digest
        return len(batch.findings)

    def get(self, finding_id: FindingId) -> FindingIndex | None:
        return self._findings.get(finding_id)

    def list_for_run(self, run_id: RunId) -> tuple[FindingIndex, ...]:
        return tuple(
            sorted(
                (finding for finding in self._findings.values() if finding.run_id == run_id),
                key=lambda finding: finding.rank,
            )
        )

    def list_for_entity(self, entity_id: EntityId) -> tuple[FindingIndex, ...]:
        return tuple(
            sorted(
                (finding for finding in self._findings.values() if finding.entity_id == entity_id),
                key=lambda finding: (finding.run_id.value, finding.rank),
            )
        )
