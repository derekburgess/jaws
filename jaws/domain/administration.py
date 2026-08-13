"""Guarded destructive-operation plans and payload-free audit records."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from .evidence import EvidenceSchemaProvenance
from .identifiers import AuditEventId, CanonicalDigest
from .serialization import canonical_digest
from .time import normalize_utc


class AuditOperation(StrEnum):
    RETENTION_APPLY = "retention_apply"
    ERASE_MANAGED_EVIDENCE = "erase_managed_evidence"


@dataclass(frozen=True, slots=True)
class AuditContext:
    """Non-secret attribution attached by a runtime interface."""

    actor: str
    interface: str
    target_database: str

    def __post_init__(self) -> None:
        for field_name in ("actor", "interface", "target_database"):
            value = getattr(self, field_name).strip()
            if not value:
                raise ValueError(f"audit {field_name.replace('_', ' ')} cannot be empty")
            object.__setattr__(self, field_name, value)


@dataclass(frozen=True, slots=True)
class AuditEvent:
    """Minimal durable metadata; never stores evidence, payloads, or credentials."""

    event_id: AuditEventId
    operation: AuditOperation
    context: AuditContext
    occurred_at: datetime
    plan_digest: CanonicalDigest
    affected_records: int

    def __post_init__(self) -> None:
        if self.affected_records < 0:
            raise ValueError("audit affected-record count cannot be negative")
        if not str(self.plan_digest).strip():
            raise ValueError("audit plan digest cannot be empty")
        object.__setattr__(self, "occurred_at", normalize_utc(self.occurred_at))


@dataclass(frozen=True, slots=True, order=True)
class AdministrationResourceCount:
    resource: str
    count: int

    def __post_init__(self) -> None:
        resource = self.resource.strip()
        if not resource:
            raise ValueError("administration resource cannot be empty")
        if self.count < 0:
            raise ValueError("administration resource count cannot be negative")
        object.__setattr__(self, "resource", resource)


@dataclass(frozen=True, slots=True)
class AdministrationPlan:
    """Exact database-bound evidence-erasure plan used as an optimistic lock."""

    target_database: str
    schema: EvidenceSchemaProvenance
    resources: tuple[AdministrationResourceCount, ...]
    relationship_count: int

    def __post_init__(self) -> None:
        database = self.target_database.strip()
        if not database:
            raise ValueError("administration target database cannot be empty")
        if self.relationship_count < 0:
            raise ValueError("administration relationship count cannot be negative")
        names = tuple(item.resource for item in self.resources)
        if len(names) != len(set(names)):
            raise ValueError("administration resources must be unique")
        object.__setattr__(self, "target_database", database)
        object.__setattr__(self, "resources", tuple(sorted(self.resources)))

    @property
    def node_count(self) -> int:
        return sum(item.count for item in self.resources)

    @property
    def digest(self) -> CanonicalDigest:
        return canonical_digest(
            {
                "operation": AuditOperation.ERASE_MANAGED_EVIDENCE.value,
                "target_database": self.target_database,
                "schema": self.schema,
                "resources": self.resources,
                "relationship_count": self.relationship_count,
            }
        )

    @property
    def confirmation(self) -> str:
        return f"ERASE {self.target_database} {self.digest}"


@dataclass(frozen=True, slots=True)
class AdministrationResult:
    plan: AdministrationPlan
    audit_event: AuditEvent
    deleted_nodes: int
    deleted_relationships: int

    def __post_init__(self) -> None:
        if self.deleted_nodes != self.plan.node_count:
            raise ValueError("administration result must match its planned node count")
        if self.deleted_relationships != self.plan.relationship_count:
            raise ValueError("administration result must match its planned relationship count")
