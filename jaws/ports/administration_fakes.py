"""Deterministic administration repository for service contracts."""

from __future__ import annotations

from dataclasses import dataclass, field

from jaws.domain import AdministrationPlan, AuditEvent

from .repositories import AdministrationConflictError


@dataclass(slots=True)
class InMemoryAdministrationRepository:
    current: AdministrationPlan
    events: list[AuditEvent] = field(default_factory=list)

    def plan(self) -> AdministrationPlan:
        return self.current

    def erase(self, expected: AdministrationPlan, audit_event: AuditEvent) -> tuple[int, int]:
        if self.current != expected:
            raise AdministrationConflictError(
                "managed evidence changed after administration was planned"
            )
        deleted = (expected.node_count, expected.relationship_count)
        self.current = AdministrationPlan(
            target_database=expected.target_database,
            schema=expected.schema,
            resources=(),
            relationship_count=0,
        )
        self.events.append(audit_event)
        return deleted

    def audit_events(self, *, limit: int = 100) -> tuple[AuditEvent, ...]:
        if limit < 1:
            raise ValueError("audit event limit must be positive")
        return tuple(reversed(self.events[-limit:]))
