"""Deterministic in-memory evidence export/import repository."""

from __future__ import annotations

from dataclasses import dataclass

from jaws.domain import EvidenceSchemaProvenance, EvidenceSnapshot

from .repositories import EvidenceImportConflictError, EvidenceSchemaConflictError

_SYSTEM_SCOPES = frozenset({"scope_pooled_all", "scope_legacy_unstamped"})


@dataclass(slots=True)
class InMemoryEvidenceRepository:
    schema: EvidenceSchemaProvenance
    evidence: EvidenceSnapshot | None = None

    def __post_init__(self) -> None:
        if self.evidence is not None and self.evidence.schema != self.schema:
            raise EvidenceSchemaConflictError("in-memory evidence schema does not match")

    def schema_provenance(self) -> EvidenceSchemaProvenance:
        return self.schema

    def snapshot(self) -> EvidenceSnapshot:
        return self.evidence or EvidenceSnapshot(schema=self.schema)

    def is_empty(self) -> bool:
        evidence = self.snapshot()
        return not (
            evidence.captures
            or evidence.packets
            or evidence.entities
            or evidence.enrichments
            or evidence.annotations
            or evidence.profiles
            or any(
                scope.scope_id.value not in _SYSTEM_SCOPES for scope in evidence.observation_scopes
            )
        )

    def restore(self, evidence: EvidenceSnapshot) -> None:
        if evidence.schema != self.schema:
            raise EvidenceSchemaConflictError("evidence schema does not match import target")
        if not self.is_empty():
            raise EvidenceImportConflictError("evidence import target is not empty")
        self.evidence = evidence
